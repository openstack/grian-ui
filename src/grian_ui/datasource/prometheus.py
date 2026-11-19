#
# SPDX-License-Identifier: Apache-2.0

"""
Prometheus datasource implementation via Aetos.
Provides access to Prometheus metrics through OpenStack observability service.
"""

import logging
import math

from keystoneauth1 import session
from keystoneauth1.identity import v3
from observabilityclient.utils import metric_utils as obs_client_utils

from grian_ui import config
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

    # Provisioning --------------------------------------------------------

    def get_provisioning_boots(self, range_key: str) -> list:
        """
        Get the boots of a range: two queries, whatever the number of boots.

        See grian_ui.datasource.prometheus_provisioning.
        """
        from grian_ui.datasource import prometheus_provisioning

        return prometheus_provisioning.get_boots(self.client, range_key)


def get_client(request) -> DatasourceClient:
    """
    Get a Prometheus telemetry client instance.

    Args:
        request: Django HTTP request with user authentication

    Returns:
        PrometheusTelemetryClient: New client instance for this request
    """
    return PrometheusTelemetryClient(request)
