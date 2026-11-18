#
# SPDX-License-Identifier: Apache-2.0

"""
Prometheus datasource implementation via Aetos.
Provides access to Prometheus metrics through OpenStack observability service.
"""

import logging
import math
import re

from keystoneauth1 import session
from keystoneauth1.identity import v3
from observabilityclient.utils import metric_utils as obs_client_utils

from grian_ui import config
from grian_ui import instance_health
from grian_ui import inventory
from grian_ui import timeseries
from grian_ui.datasource.api import DatasourceClient


LOG = logging.getLogger(__name__)

STALENESS_WINDOW = "10m"
#: Timeout of the requests to Aetos, in seconds (per connection attempt and
#: per read, as for every keystoneauth session), so a hung backend fails the
#: query instead of holding a Horizon worker thread indefinitely. A
#: computation running several queries can still take longer than
#: grian_ui.cache.MAX_COMPUTE_SECONDS; another request then takes the lock
#: over.
QUERY_TIMEOUT = 30


def number(value, ndigits=None):
    """
    Convert a Prometheus sample value to a finite float or None.

    Values used for status decisions should be kept unrounded; pass
    ``ndigits`` only for display data such as chart points.
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result if ndigits is None else round(result, ndigits)


# Instance metrics (Ceilometer compute agent, cumulative counters).
INSTANCE_CPU_METRIC = "ceilometer_cpu"
INSTANCE_METRICS = {
    "cpu_delay": "ceilometer_cpu_delay",
    "vcpus": "ceilometer_vcpus",
    "swap": "ceilometer_memory_swap_(in|out)",
    "disk_latency": "ceilometer_disk_device_(read|write)_latency",
    "disk_requests": "ceilometer_disk_device_(read|write)_requests",
    "packet_loss": "ceilometer_network_(incoming|outgoing)_packets_(drop|error)",
}
INSTANCE_ID_LABEL = "instance_id"
_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_LAST_UUID_RE = re.compile(f".*({_UUID})")


def instance_id_from_resource(resource):
    """
    Instance UUID of a resource label.

    The resource is ``<uuid>`` (cpu, memory), ``<uuid>-<disk>`` (disks) or
    ``<libvirt name>-<uuid>-<vnic>`` (interfaces): the last UUID is the
    instance.
    """
    match = _LAST_UUID_RE.match(resource or "")
    return match.group(1) if match else None


def _with_instance_id(expr):
    """Copy the instance UUID of the resource label into instance_id."""
    return (
        f'label_replace({expr}, "{INSTANCE_ID_LABEL}", "$1", "resource", '
        f'".*({_UUID}).*")'
    )


def _instance_matchers(instance_ids):
    """
    Resource matchers for at most a page of instances.

    Returns:
        dict: kind -> matcher, for the "instance", "disk" and "interface"
        resource formats, or None when no id is a valid UUID (so user
        input can never change the structure of the query)
    """
    valid = sorted({i for i in instance_ids if inventory.is_instance_id(i)})
    if not valid:
        return None
    ids = "|".join(valid)
    return {
        "instance": f'resource=~"{ids}"',
        "disk": f'resource=~"({ids})-.+"',
        "interface": f'resource=~".*({ids}).*"',
    }


def _sel(name, matcher=None):
    parts = [f'__name__=~"{name}"']
    if matcher:
        parts.append(matcher)
    return "{" + ", ".join(parts) + "}"


def instance_symptom_expr(symptom, window, matchers=None, idle_zero=False):
    """
    PromQL for one symptom measure, one series per instance.

    The disk measure is the worst device of each instance (use
    :func:`disk_latency_by_device_expr` to know which device).
    """
    m = matchers or {}
    by = f"sum by ({INSTANCE_ID_LABEL})"
    if symptom == instance_health.CPU:
        delay = _with_instance_id(
            f"rate({_sel(INSTANCE_METRICS['cpu_delay'], m.get('instance'))}"
            f"[{window}])"
        )
        vcpus = _with_instance_id(
            f"last_over_time({_sel(INSTANCE_METRICS['vcpus'], m.get('instance'))}"
            f"[{instance_health.silence_window(window)}])"
        )
        return (
            f"100 * {by} ({delay}) / 1e9 / on ({INSTANCE_ID_LABEL}) "
            f"max by ({INSTANCE_ID_LABEL}) ({vcpus})"
        )
    if symptom == instance_health.MEMORY:
        return _per_instance_rate("swap", window, m.get("instance"))
    if symptom == instance_health.NETWORK:
        return _per_instance_rate("packet_loss", window, m.get("interface"))
    if symptom == instance_health.DISK:
        per_device = disk_latency_by_device_expr(
            window, m.get("disk"), idle_zero
        )
        return (
            f"max by ({INSTANCE_ID_LABEL}) ({_with_instance_id(per_device)})"
        )
    raise ValueError(f"not a metric symptom: {symptom}")


def _per_instance_rate(metric, window, matcher):
    rate = _with_instance_id(
        f"rate({_sel(INSTANCE_METRICS[metric], matcher)}[{window}])"
    )
    return f"sum by ({INSTANCE_ID_LABEL}) ({rate})"


def disk_latency_by_device_expr(window, matcher=None, idle_zero=False):
    """
    Average ms per request (reads and writes) of each instance disk.

    A disk without requests has no latency (0/0, dropped as NaN); with
    ``idle_zero`` (charts) it reads 0 ms instead, so an idle disk is not
    drawn like a missing one.
    """
    latency = _sel(INSTANCE_METRICS["disk_latency"], matcher)
    requests = _sel(INSTANCE_METRICS["disk_requests"], matcher)
    rate = f"sum by (resource) (rate({requests}[{window}]))"
    if idle_zero:
        return (
            f"(sum by (resource) (rate({latency}[{window}])) "
            f"/ ({rate} > 0) / 1e6) or (0 * {rate})"
        )
    return f"sum by (resource) (rate({latency}[{window}])) / {rate} / 1e6"


class PrometheusTelemetryClient(DatasourceClient):
    """
    Prometheus telemetry client to retrieves data from the OSP deployment
    """

    def __init__(self, request):
        """
        Initialize Prometheus Telemetry client via Aetos.

        Args:
            request: Django HTTP request containing user authentication

        Note:
            Uses get_prom_client_from_keystone for proper Aetos/Prometheus
            client initialization with Keystone session.
            A new client instance should be created per page request,
            but can be reused for multiple metric queries within that request.
        """
        auth_url = config.get_openstack_keystone_url()
        token = request.user.token.id
        project_id = request.user.project_id
        domain_id = request.user.domain_id
        interface = config.get_openstack_endpoint_type()

        auth = v3.Token(
            auth_url=auth_url,
            token=token,
            project_id=project_id,
            domain_id=domain_id,
        )

        sess = session.Session(auth=auth, timeout=QUERY_TIMEOUT)

        adapter_options = {
            "service_type": "metric-storage",
            "region_name": request.user.services_region,
            "interface": interface,
        }

        # discovers the Aetos endpoint via service catalog
        self.client = obs_client_utils.get_prom_client_from_keystone(
            sess, adapter_options=adapter_options
        )

    def get_telemetry_overview(self) -> dict:
        """
        Get telemetry overview data.

        Aggregates multiple Prometheus queries to build a dashboard overview.
        Returns computed metrics rather than raw metric list.
        """
        overview = {}

        try:
            # Query for vCPUs count
            cpu_result = self.client.query(query="sum(ceilometer_vcpus)")
            if cpu_result and len(cpu_result) > 0:
                overview["cpu_usage_avg"] = round(
                    float(cpu_result[0].value), 1
                )
            else:
                overview["cpu_usage_avg"] = 0.0

            # Query for memory usage in MB
            memory_result = self.client.query(
                query="sum(ceilometer_memory_usage)"
            )
            if memory_result and len(memory_result) > 0:
                # Convert MB to GB for display
                overview["memory_usage_avg"] = round(
                    float(memory_result[0].value) / 1024, 1
                )
            else:
                overview["memory_usage_avg"] = 0.0

            # Query for total storage capacity
            total_storage_result = self.client.query(
                query="sum(ceilometer_volume_provider_pool_capacity_total)"
            )
            if total_storage_result and len(total_storage_result) > 0:
                overview["total_storage"] = int(
                    float(total_storage_result[0].value)
                )
            else:
                overview["total_storage"] = 0

            # Query for allocated storage
            allocated_storage_result = self.client.query(
                query="sum(ceilometer_volume_provider_pool_capacity_allocated)"
            )
            if allocated_storage_result and len(allocated_storage_result) > 0:
                overview["allocated_storage"] = int(
                    float(allocated_storage_result[0].value)
                )
            else:
                overview["allocated_storage"] = 0

        except Exception as e:
            # Return partial data if some queries fail
            overview["error"] = str(e)

        return overview

    def get_volumes_count(self) -> int:
        """
        Get telemetry volumes count.

        Returns:
            int: Current count of volumes from Ceilometer metrics
        """
        result = self.client.query(
            query=f"count(last_over_time(ceilometer_volume_size[{STALENESS_WINDOW}]))"
        )
        if result and len(result) > 0:
            return int(float(result[0].value))
        return 0

    def get_snapshots_count(self) -> int:
        """
        Get telemetry snapshots count.

        Returns:
            int: Current count of snapshots from Ceilometer metrics
        """
        result = self.client.query(
            query=f"count(last_over_time(ceilometer_volume_snapshot_size[{STALENESS_WINDOW}]))"
        )
        if result and len(result) > 0:
            return int(float(result[0].value))
        return 0

    def get_storage_pools(self) -> list:
        """
        Get storage pools information from Ceilometer metrics.

        Returns:
            list: List of storage pool dictionaries with capacity metrics
        """
        pools = []

        try:
            # Get all unique pool resources
            # We'll use the total capacity metric to identify pools
            total_result = self.client.query(
                query="ceilometer_volume_provider_pool_capacity_total"
            )

            if not total_result:
                return pools

            # For each pool, gather all metrics
            for pool_metric in total_result:
                pool_name = pool_metric.labels.get("resource", "unknown")

                pool_data = {
                    "name": pool_name,
                    "total": float(pool_metric.value),
                    "allocated": 0.0,
                    "free": 0.0,
                    "provisioned": 0.0,
                }

                # Get allocated capacity for this pool
                allocated_query = f'ceilometer_volume_provider_pool_capacity_allocated{{resource="{pool_name}"}}'
                allocated_result = self.client.query(query=allocated_query)
                if allocated_result and len(allocated_result) > 0:
                    pool_data["allocated"] = float(allocated_result[0].value)

                # Get free capacity for this pool
                free_query = f'ceilometer_volume_provider_pool_capacity_free{{resource="{pool_name}"}}'
                free_result = self.client.query(query=free_query)
                if free_result and len(free_result) > 0:
                    pool_data["free"] = float(free_result[0].value)

                # Get provisioned capacity for this pool
                provisioned_query = f'ceilometer_volume_provider_pool_capacity_provisioned{{resource="{pool_name}"}}'
                provisioned_result = self.client.query(query=provisioned_query)
                if provisioned_result and len(provisioned_result) > 0:
                    pool_data["provisioned"] = float(
                        provisioned_result[0].value
                    )

                pools.append(pool_data)

        except Exception:
            # Return empty list on error
            return []

        return pools

    def get_capacity_predictions(self) -> list:
        """
        Predict when storage pools will run out of space.

        Uses predict_linear to forecast when free capacity will hit zero
        based on the last 7 days of data, looking 30 days into the future.

        Returns:
            list: List of predictions per pool with:
                - name: pool name
                - days_until_full: predicted days until capacity hits zero (None if not predicted to fill)
                - predicted_value: the predicted free capacity in 30 days
                - current_free: current free capacity
        """
        predictions = []

        try:
            # Get all unique pool resources
            total_result = self.client.query(
                query="ceilometer_volume_provider_pool_capacity_total"
            )

            if not total_result:
                return predictions

            # For each pool, calculate prediction
            for pool_metric in total_result:
                pool_name = pool_metric.labels.get("resource", "unknown")

                # Get current free capacity
                free_query = f'ceilometer_volume_provider_pool_capacity_free{{resource="{pool_name}"}}'
                free_result = self.client.query(query=free_query)
                current_free = 0.0
                if free_result and len(free_result) > 0:
                    current_free = float(free_result[0].value)

                # Predict free capacity in 30 days based on 7 days of history
                # predict_linear(metric[7d], 30 * 86400) predicts value 30 days from now
                predict_query = f'predict_linear(ceilometer_volume_provider_pool_capacity_free{{resource="{pool_name}"}}[7d], 30 * 86400)'

                predict_result = self.client.query(query=predict_query)

                prediction_data = {
                    "name": pool_name,
                    "current_free": current_free,
                    "predicted_value": None,
                    "days_until_full": None,
                    "will_run_out": False,
                }

                if predict_result and len(predict_result) > 0:
                    predicted_value = float(predict_result[0].value)
                    prediction_data["predicted_value"] = predicted_value

                    # If predicted to go below zero, calculate when
                    if predicted_value < 0 and current_free > 0:
                        # Linear interpolation to find when it crosses zero
                        # current_free - (days_until_full / 30) * (current_free - predicted_value) = 0
                        # Solve for days_until_full
                        decline_per_day = (current_free - predicted_value) / 30
                        if decline_per_day > 0:
                            days_until_full = current_free / decline_per_day
                            prediction_data["days_until_full"] = round(
                                days_until_full, 1
                            )
                            prediction_data["will_run_out"] = True

                predictions.append(prediction_data)

        except Exception:
            # Return empty list on error
            return []

        return predictions

    def query_matrix(self, query, ndigits=None, time=None):
        """
        Run an instant query that returns a range vector (e.g. a subquery).

        Aetos only exposes the instant query endpoint, and
        ``PrometheusAPIClient.query`` only parses instant vectors, so the
        raw response is decoded here. Non-finite values are dropped.

        Args:
            ndigits: round values (for display data), or None
            time: evaluation time (epoch seconds), or None for "now". Pass
                the time the chart grid is built from
                (:func:`grian_ui.timeseries.range_grid`), so the points and
                the grid line up even if the clocks differ.

        Returns:
            list: ``[(labels, [[timestamp, value], ...]), ...]``
        """
        params = {"query": query}
        # PrometheusAPIClient has no public call that returns the raw
        # response; _get is the method its own query() uses.
        if time is not None:
            params["time"] = f"{time:.3f}"
        decoded = self.client._get("query", params)
        data = decoded.get("data", {})
        if data.get("resultType") != "matrix":
            LOG.warning(
                "Expected a range vector, got %s, for query %s",
                data.get("resultType"),
                query,
            )
            return []
        series = []
        for result in data.get("result", []):
            points = []
            for timestamp, value in result.get("values", []):
                sample = number(value, ndigits)
                if sample is not None:
                    points.append([float(timestamp), sample])
            series.append((result.get("metric", {}), points))
        return series

    def get_reporting_instances(self, window=None) -> dict:
        """One query: every instance with a cpu sample in ``window``."""
        query = (
            "group by (resource, resource_name, project) "
            f"(last_over_time({INSTANCE_CPU_METRIC}"
            f"[{window or instance_health.STALE_AFTER}]))"
        )
        reporting = {}
        for metric in self.client.query(query=query) or []:
            instance_id = metric.labels.get("resource")
            if not inventory.is_instance_id(instance_id):
                continue
            # resource_name is "<display name>:<libvirt name>"
            name = metric.labels.get("resource_name") or ""
            reporting[instance_id] = {
                "name": name.rpartition(":")[0] or name or None,
                "project_id": metric.labels.get("project"),
            }
        return reporting

    def get_instance_symptoms(self, thresholds, rate_window) -> dict:
        """
        Four queries, each filtered server-side by the warning threshold.
        """
        symptoms = {}
        for symptom in (
            instance_health.CPU,
            instance_health.MEMORY,
            instance_health.NETWORK,
        ):
            prefix = instance_health.SYMPTOM_THRESHOLDS[symptom][0]
            warning = thresholds[f"{prefix}_warning"]
            query = (
                f"({instance_symptom_expr(symptom, rate_window)}) > {warning}"
            )
            found = {}
            for metric in self.client.query(query=query) or []:
                instance_id = metric.labels.get(INSTANCE_ID_LABEL)
                value = number(metric.value)
                # IDs end up in the page (and in queries): UUIDs only
                if inventory.is_instance_id(instance_id) and value is not None:
                    found[instance_id] = value
            symptoms[symptom] = found

        warning = thresholds["disk_latency_warning"]
        query = f"({disk_latency_by_device_expr(rate_window)}) > {warning}"
        disks = {}
        for metric in self.client.query(query=query) or []:
            resource = metric.labels.get("resource", "")
            instance_id = instance_id_from_resource(resource)
            value = number(metric.value)
            if instance_id is None or value is None:
                continue
            if value > disks.get(instance_id, {}).get("value", -1):
                device = resource.rpartition(instance_id)[2].lstrip("-")
                disks[instance_id] = {"value": value, "device": device or None}
        symptoms[instance_health.DISK] = disks
        return symptoms

    def get_instance_last_seen(self, instance_ids) -> dict:
        """One query for at most a page of instances."""
        matchers = _instance_matchers(instance_ids)
        if matchers is None:
            return {}
        query = (
            "max by (resource) (max_over_time(timestamp("
            f"{_sel(INSTANCE_CPU_METRIC, matchers['instance'])})"
            f"[{instance_health.LAST_SEEN_LOOKBACK}:1m]))"
        )
        last_seen = {}
        for metric in self.client.query(query=query) or []:
            value = number(metric.value)
            if value is not None:
                last_seen[metric.labels.get("resource")] = value
        return last_seen

    def get_instance_trends(
        self, instance_id, range_key, rate_window, at=None
    ) -> dict:
        """One query returning the four measures of one instance."""
        trends = {s: [] for s in instance_health.METRIC_SYMPTOMS}
        matchers = _instance_matchers([instance_id])
        if matchers is None:
            return trends
        _key, window, step = timeseries.get_range(range_key)
        # charts: an idle disk reads 0 ms, not a gap
        union = " or ".join(
            "label_replace("
            + instance_symptom_expr(s, rate_window, matchers, idle_zero=True)
            + f', "field", "{s}", "", "")'
            for s in instance_health.METRIC_SYMPTOMS
        )
        for labels, points in self.query_matrix(
            f"({union})[{window}:{step}]", ndigits=2, time=at
        ):
            field = labels.get("field")
            if field in trends:
                trends[field] = points
        return trends

    def get_symptom_trends(
        self, symptom, instance_ids, range_key, rate_window, at=None
    ) -> dict:
        """One query for at most a page of instances."""
        matchers = _instance_matchers(instance_ids)
        if matchers is None or symptom not in instance_health.METRIC_SYMPTOMS:
            return {}
        _key, window, step = timeseries.get_range(range_key)
        expr = instance_symptom_expr(
            symptom, rate_window, matchers, idle_zero=True
        )
        trends = {}
        for labels, points in self.query_matrix(
            f"({expr})[{window}:{step}]", ndigits=2, time=at
        ):
            instance_id = labels.get(INSTANCE_ID_LABEL)
            if instance_id:
                trends[instance_id] = points
        return trends


def get_client(request) -> DatasourceClient:
    """
    Get a Prometheus telemetry client instance.

    Args:
        request: Django HTTP request with user authentication

    Returns:
        PrometheusTelemetryClient: New client instance for this request
    """
    return PrometheusTelemetryClient(request)
