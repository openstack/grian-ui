#
# SPDX-License-Identifier: Apache-2.0

"""
Prometheus datasource implementation via Aetos.
Provides access to Prometheus metrics through OpenStack observability service.
"""

import math  # noqa: I001
import re

from keystoneauth1.identity import v3  # noqa: I001
from keystoneauth1 import session  # noqa: I001
from observabilityclient.utils import metric_utils as obs_client_utils

from grian_ui import compute_health
from grian_ui import config
from grian_ui.datasource.api import DatasourceClient


STALENESS_WINDOW = "10m"

# Compute host CPU metrics (Nova compute monitors -> Ceilometer).
COMPUTE_CPU_METRIC = "ceilometer_compute_node_cpu_percent"
COMPUTE_IOWAIT_METRIC = "ceilometer_compute_node_cpu_iowait_percent"
COMPUTE_DETAIL_METRICS = {
    "ceilometer_compute_node_cpu_percent": "cpu",
    "ceilometer_compute_node_cpu_user_percent": "user",
    "ceilometer_compute_node_cpu_kernel_percent": "kernel",
    "ceilometer_compute_node_cpu_iowait_percent": "iowait",
    "ceilometer_compute_node_cpu_idle_percent": "idle",
    "ceilometer_compute_node_cpu_frequency": "frequency",
}
# The host is the notification publisher. Nova publishes
# compute.metrics.update with host=<hypervisor hostname> (nodename), so
# vm_instance="compute.<hypervisor hostname>", which can differ from the
# nova-compute service host.
COMPUTE_HOST_LABEL = "vm_instance"
COMPUTE_HOST_PREFIX = "compute."
# Host names accepted when building per-host queries.
_HOST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def _host_from_labels(labels):
    value = labels.get(COMPUTE_HOST_LABEL, "")
    if value.startswith(COMPUTE_HOST_PREFIX):
        return value[len(COMPUTE_HOST_PREFIX) :]
    return value or None


def _host_selector(names):
    """
    Build a PromQL label matcher for the given host names.

    Names that are not valid host names are dropped, so user input can never
    change the structure of the query.
    """
    valid = [n for n in names if _HOST_NAME_RE.match(n or "")]
    if not valid:
        return None
    # re.escape for the RE2 regex, then escape backslashes for the PromQL
    # string literal.
    alternatives = "|".join(re.escape(n).replace("\\", "\\\\") for n in valid)
    prefix = re.escape(COMPUTE_HOST_PREFIX).replace("\\", "\\\\")
    return f'{COMPUTE_HOST_LABEL}=~"{prefix}({alternatives})"'


def _number(value, ndigits=None):
    """
    Convert a Prometheus sample value to a finite float or None.

    Values used for status decisions are kept unrounded; pass ``ndigits``
    only for display data such as chart points.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number if ndigits is None else round(number, ndigits)


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

        sess = session.Session(auth=auth)

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

    def _query_matrix(self, query):
        """
        Run an instant query that returns a range vector (e.g. a subquery).

        Aetos only exposes the instant query endpoint, and
        ``PrometheusAPIClient.query`` only parses instant vectors, so the
        raw response is decoded here.

        Returns:
            list: ``[(labels, [[timestamp, value], ...]), ...]``
        """
        decoded = self.client._get("query", {"query": query})
        data = decoded.get("data", {})
        if data.get("resultType") != "matrix":
            return []
        series = []
        for result in data.get("result", []):
            points = []
            for timestamp, value in result.get("values", []):
                number = _number(value, 1)
                if number is not None:
                    points.append([float(timestamp), number])
            series.append((result.get("metric", {}), points))
        return series

    def get_compute_host_snapshot(self) -> dict:
        """
        Get one 15 minute average per compute host, plus stale hosts.

        Uses three queries whatever the number of hosts. Each result has one
        series per host carrying only the host label.
        """
        host = COMPUTE_HOST_LABEL
        fresh = (
            f"max by ({host}) "
            f"(last_over_time({COMPUTE_CPU_METRIC}"
            f"[{compute_health.STALE_AFTER}]))"
        )
        window = compute_health.AVERAGE_WINDOW
        cpu_query = (
            f"max by ({host}) (avg_over_time({COMPUTE_CPU_METRIC}[{window}])) "
            f"and on ({host}) {fresh}"
        )
        iowait_query = (
            f"max by ({host}) "
            f"(avg_over_time({COMPUTE_IOWAIT_METRIC}[{window}])) "
            f"and on ({host}) {fresh}"
        )
        stale_query = (
            f"max by ({host}) (max_over_time(timestamp({COMPUTE_CPU_METRIC})"
            f"[{compute_health.STALE_LOOKBACK}:{compute_health.STALE_STEP}])) "
            f"unless on ({host}) {fresh}"
        )

        hosts = {}
        for metric in self.client.query(query=cpu_query) or []:
            name = _host_from_labels(metric.labels)
            if name:
                hosts[name] = {
                    "name": name,
                    "cpu_avg": _number(metric.value),
                    "iowait_avg": None,
                }
        for metric in self.client.query(query=iowait_query) or []:
            name = _host_from_labels(metric.labels)
            if name in hosts:
                hosts[name]["iowait_avg"] = _number(metric.value)

        stale = []
        for metric in self.client.query(query=stale_query) or []:
            name = _host_from_labels(metric.labels)
            if name and name not in hosts:
                stale.append({"name": name, "last_seen": float(metric.value)})

        return {"hosts": list(hosts.values()), "stale": stale}

    def get_compute_host_details(self, names: list) -> dict:
        """
        Get the latest CPU breakdown for at most a page of hosts.

        Values are the last samples within STALE_AFTER, the same freshness
        window as the status logic, so a host that just stopped reporting
        still shows its last known values. Two queries.
        """
        selector = _host_selector(names)
        if selector is None:
            return {}
        host = COMPUTE_HOST_LABEL
        window = compute_health.STALE_AFTER
        details = {}

        # Range functions drop the metric name, so tag each metric with a
        # "field" label and union them in a single query.
        query = " or ".join(
            f'label_replace(max by ({host}) (last_over_time('
            f'{metric}{{{selector}}}[{window}])), "field", "{field}", "", "")'
            for metric, field in COMPUTE_DETAIL_METRICS.items()
        )
        for metric in self.client.query(query=query) or []:
            name = _host_from_labels(metric.labels)
            field = metric.labels.get("field")
            if name and field in _empty_details():
                details.setdefault(name, _empty_details())[field] = _number(
                    metric.value
                )

        last_seen_query = (
            f"max by ({host}) (max_over_time(timestamp("
            f"{COMPUTE_CPU_METRIC}{{{selector}}})[{window}:15s]))"
        )
        for metric in self.client.query(query=last_seen_query) or []:
            name = _host_from_labels(metric.labels)
            if name:
                details.setdefault(name, _empty_details())["last_seen"] = (
                    float(metric.value)
                )
        return details

    def get_compute_host_trends(self, names: list, range_key: str) -> dict:
        """Get the CPU trend of at most a page of hosts (one query)."""
        selector = _host_selector(names)
        if selector is None:
            return {}
        _key, window, step = compute_health.get_range(range_key)
        query = (
            f"max by ({COMPUTE_HOST_LABEL}) "
            f"(avg_over_time({COMPUTE_CPU_METRIC}{{{selector}}}[{step}]))"
            f"[{window}:{step}]"
        )
        trends = {}
        for labels, points in self._query_matrix(query):
            name = _host_from_labels(labels)
            if name:
                trends[name] = points
        return trends

    def get_compute_fleet_trend(self, range_key: str) -> dict:
        """
        Get the fleet median, 95th percentile and max CPU over time.

        One query returning three series, whatever the number of hosts.
        """
        _key, window, step = compute_health.get_range(range_key)
        # one value per host, so duplicated series (several notification
        # agents or sg-core replicas) do not skew the percentiles
        inner = (
            f"max by ({COMPUTE_HOST_LABEL}) "
            f"(avg_over_time({COMPUTE_CPU_METRIC}[{step}]))"
        )
        query = (
            f'(label_replace(quantile(0.5, {inner}), "stat", "p50", "", "")'
            f' or label_replace(quantile(0.95, {inner}), "stat", "p95", "", "")'
            f' or label_replace(max({inner}), "stat", "max", "", ""))'
            f"[{window}:{step}]"
        )
        trend = {"p50": [], "p95": [], "max": []}
        for labels, points in self._query_matrix(query):
            stat = labels.get("stat")
            if stat in trend:
                trend[stat] = points
        return trend


def _empty_details():
    return {
        "cpu": None,
        "user": None,
        "kernel": None,
        "iowait": None,
        "idle": None,
        "frequency": None,
        "last_seen": None,
    }


def get_client(request) -> DatasourceClient:
    """
    Get a Prometheus telemetry client instance.

    Args:
        request: Django HTTP request with user authentication

    Returns:
        PrometheusTelemetryClient: New client instance for this request
    """
    return PrometheusTelemetryClient(request)
