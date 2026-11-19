#
# SPDX-License-Identifier: Apache-2.0

"""
Prometheus queries of the Provisioning dashboard.

Ceilometer turns Nova's ``compute.instance.create.end`` notification into
one ``compute.instance.booting.time`` sample per instance: the seconds from
the create request (``created_at``) to the instance running
(``launched_at``). sg-core keeps exposing the last value for a few
minutes, so Prometheus stores 10 to 20 identical samples per boot: boots
are counted per instance (``resource``), never per sample.

Unlike the other instance metrics, whose ``vm_instance`` label is a hashed
host ID, this metric's ``vm_instance`` is the plain nova-compute host name.
"""

from grian_ui import provisioning
from grian_ui.datasource.prometheus import number


BOOT_METRIC = "ceilometer_compute_instance_booting_time"
#: Labels kept per boot. Several notification agents or sg-core replicas
#: can export the same boot with other labels (``publisher``): grouping by
#: these labels counts it once.
BOOT_LABELS = ("resource", "resource_name", "project", "vm_instance")


def boot_queries(range_key):
    """
    The two queries returning the boots of a range.

    Returns:
        tuple: (seconds query, first seen query). Both return one series
        per instance booted in the range, the first its boot time, the
        second the time its first sample was scraped (to the range's step).
    """
    _key, window, step = provisioning.get_range(range_key)
    labels = ", ".join(BOOT_LABELS)
    seconds = f"max by ({labels}) (last_over_time({BOOT_METRIC}[{window}]))"
    # timestamp() returns the time of the sample each step sees, so the
    # earliest one is the first sample, found within one step
    first_seen = (
        f"min by (resource) "
        f"(min_over_time(timestamp({BOOT_METRIC})[{window}:{step}]))"
    )
    return seconds, first_seen


def get_boots(prom, range_key):
    """
    Get the boots of a range (two queries, whatever the number of boots).

    Args:
        prom: an observabilityclient PrometheusAPIClient

    Returns:
        list: boots, see :mod:`grian_ui.provisioning`
    """
    seconds_query, first_seen_query = boot_queries(range_key)
    first_seen = {}
    for metric in prom.query(query=first_seen_query) or []:
        resource = metric.labels.get("resource")
        at = number(metric.value)
        if resource and at is not None:
            first_seen[resource] = at

    boots = {}
    for metric in prom.query(query=seconds_query) or []:
        labels = metric.labels
        resource = labels.get("resource")
        seconds = number(metric.value)
        if not resource or seconds is None or seconds < 0:
            continue
        at = first_seen.get(resource)
        if at is None:
            # the instance's samples aged out between the two queries
            continue
        known = boots.get(resource)
        if known is not None and known["seconds"] >= seconds:
            continue
        boots[resource] = {
            "id": resource,
            "name": labels.get("resource_name") or None,
            "project_id": labels.get("project") or None,
            "host": labels.get("vm_instance") or None,
            "seconds": seconds,
            "at": at,
        }
    return list(boots.values())
