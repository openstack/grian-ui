==============================
Compute Host Health Dashboard
==============================

The Compute Host Health dashboard shows cloud administrators which compute
hosts are overloaded, waiting on storage, or no longer reporting. It is
accessible under **Admin > Telemetry > Compute Host Health** and requires the
``openstack.roles.admin`` permission.

The dashboard is designed for clouds with hundreds or thousands of compute
hosts: every panel renders a fixed amount of data (summary numbers, at most
10 hosts, a handful of CPU bands, three fleet series) whatever the number of
hosts.

Metrics originate from the Nova CPU monitor, are converted to meters by
`Ceilometer <https://docs.openstack.org/ceilometer/latest/>`_, stored in
Prometheus via `sg-core <https://github.com/infrawatch/sg-core>`_ and queried
through the `Aetos <https://opendev.org/openstack/aetos>`_ observability
proxy (``service_type: metric-storage``).

This page covers requirements, data sources and configuration. For a guide
to using the dashboard, see :doc:`user-compute-host-health`.


Requirements
============

The ``compute.node.cpu.*`` meters are only produced when the Nova CPU monitor
is enabled on the compute nodes, in ``nova.conf``:

.. code-block:: ini

   [DEFAULT]
   compute_monitors = cpu.virt_driver

``compute.metrics.update`` is an unversioned notification, which Nova emits
with its default ``[notifications] notification_format = unversioned`` (and
with ``both``); no other notification setting is needed.

In DevStack::

   [[post-config|$NOVA_CONF]]
   [DEFAULT]
   compute_monitors=cpu.virt_driver

Nova then emits a ``compute.metrics.update`` notification about once a minute
per host, which the Ceilometer notification agent turns into the meters
below.


Metrics
=======

.. list-table::
   :header-rows: 1
   :widths: 45 15 40

   * - Prometheus metric
     - Unit
     - Used for
   * - ``ceilometer_compute_node_cpu_percent``
     - %
     - Status, averages, fleet trend, CPU bands, host trends
   * - ``ceilometer_compute_node_cpu_iowait_percent``
     - %
     - Status, highest I/O wait, top hosts by I/O wait
   * - ``ceilometer_compute_node_cpu_user_percent``
     - %
     - CPU breakdown (top hosts, host lookup)
   * - ``ceilometer_compute_node_cpu_kernel_percent``
     - %
     - CPU breakdown (top hosts, host lookup)
   * - ``ceilometer_compute_node_cpu_idle_percent``
     - %
     - Host lookup
   * - ``ceilometer_compute_node_cpu_frequency``
     - MHz
     - Top hosts, host lookup

The host name is taken from the ``vm_instance`` label, which holds the
notification publisher. Nova publishes ``compute.metrics.update`` as
``compute.<hypervisor hostname>``, so the dashboard shows hypervisor
hostnames (``openstack hypervisor list``), which can differ from the
``nova-compute`` service host (``openstack compute service list``).


Host status
===========

Status uses averages over the last **15 minutes**:

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Status
     - Rule (defaults)
   * - Critical
     - CPU ≥ 90% or I/O wait ≥ 20%
   * - Warning
     - CPU ≥ 75% or I/O wait ≥ 10%
   * - Stale
     - Seen in the last 24 hours but not in the last 10 minutes
   * - Unknown
     - Reporting, but without a valid CPU value (e.g. NaN)
   * - Healthy
     - Otherwise

How quickly a dead host becomes **stale**: sg-core keeps exposing a host's
series for twice its metric interval after the last notification (100 s
interval, ``expirationMultiple: 2``, so about 200 s), then Prometheus marks
the series stale. The host is reported stale once it has had no sample for
10 minutes, so roughly 13–14 minutes after it stopped reporting.

"Last seen" is the last time Prometheus scraped a value for the host. For
reporting hosts it is within one scrape interval of the last notification;
for stale hosts it is computed at a 5 minute resolution, so it may be up to
5 minutes early.

.. note::

   Hosts that have not reported for more than 24 hours are no longer known to
   the dashboard: the host list comes from the metrics, not from Nova. Use
   ``openstack hypervisor list`` and ``openstack compute service list`` to
   find hypervisors and compute services that are down
   for longer than that.

If **no** host is reporting while hosts were seen recently, the dashboard
says so above the cards. When at least 3 hosts stopped within about
10 minutes of each other, it points at the telemetry pipeline (Ceilometer
notification agent, sg-core or Prometheus) as the most likely cause.


Panels
======

All panels are HTMX partials refreshing every **30 seconds**. The time range
buttons (1h, 6h, 24h, 7d) apply to the trend charts; status always uses the
last 15 minutes.

Summary cards
-------------

* **Hosts reporting**: reporting hosts / hosts seen in the last 24 hours.
* **Fleet CPU: median / p95** of the per-host 15 minute averages.
* **Hosts needing attention**: critical + warning + stale.
* **Highest I/O wait** and the host it was measured on.

Attention list
--------------

The 10 worst hosts (critical first, then warning, then stale), with the
reason. The total counts are always shown even when more than 10 hosts need
attention.

Fleet CPU usage over time
-------------------------

Median, 95th percentile and maximum CPU across all reporting hosts, with the
warning and critical thresholds. One query returning three series:

.. code-block:: text

   (label_replace(quantile(0.5, avg_over_time(M[step])), "stat", "p50", "", "")
    or label_replace(quantile(0.95, avg_over_time(M[step])), "stat", "p95", "", "")
    or label_replace(max(avg_over_time(M[step])), "stat", "max", "", ""))[range:step]

where ``M`` is ``ceilometer_compute_node_cpu_percent``. Steps are 1m (1h),
5m (6h), 15m (24h) and 1h (7d).

Hosts by CPU usage
------------------

Number of hosts in each CPU band. The bands follow the thresholds
(0–25, 25–50, 50–75, 75–90, ≥ 90% with the defaults). Clicking a bar lists
the busiest hosts of that band in the top hosts table.

Top hosts
---------

At most 10 hosts, sorted by 15 minute average CPU, by I/O wait, by oldest
"last seen" (stale hosts) or restricted to a CPU band. Each row shows the
current user / kernel / I/O wait breakdown, frequency, a trend line and the
last time the host was seen. Clicking a host name opens it in host lookup.

Look up a host
--------------

Status, CPU breakdown and trend of any single host, compared with the fleet
median. The box suggests host names as you type (names starting with the
text first, then names containing it, at most 20); with an empty box it
suggests the hosts needing attention first. Picking a suggestion shows the
host immediately. Suggestions are served from the cached snapshot and cost
no extra queries.


Configuration
=============

Thresholds can be overridden per key in the Horizon ``local_settings``:

.. code-block:: python

   GRIAN_PLUGIN = {
       "datasource": "prometheus",
       "compute_host_thresholds": {
           "cpu_warning": 80,
           "cpu_critical": 95,
           "iowait_warning": 10,
           "iowait_critical": 20,
       },
   }

Values are percentages from 0 to less than 100; a warning threshold may not
be greater than the matching critical threshold.


Scaling
=======

* **Bounded rendering.** The browser receives at most 10 table rows, 10
  trend lines, a few CPU bands and 3 fleet series, whatever the number of
  hosts.
* **No per-host queries.** One refresh cycle (every 30 seconds, only while
  the browser tab is visible) runs:

  .. list-table::
     :header-rows: 1
     :widths: 40 60

     * - Data
       - Queries
     * - Snapshot (shared by the summary, the CPU bands and the top hosts)
       - 3 fleet-wide queries (15 minute CPU and I/O wait averages, stale
         hosts), each returning one compact series per host
     * - Fleet trend
       - 1 query returning 3 series
     * - Top hosts
       - 2 queries for the details and 1 for the trends, limited to the at
         most 10 displayed hosts
     * - Host lookup (when a host is displayed)
       - 2 queries for its details and 1 for its trend
     * - Host name suggestions (while typing)
       - none (cached snapshot), at most 20 names

* **Shared cache.** The snapshot (25 seconds) and the fleet trend (25 seconds
  to 15 minutes depending on the range) are cached in the Horizon cache and
  shared by the partials and by all administrators of a region. A short
  lock makes the partials of one refresh, which fire together, wait (at most
  5 seconds) for a single computation instead of querying in parallel. A
  failed computation is remembered for 5 seconds so it is not retried by
  the other partials, and a request that times out waiting never queries
  again itself: a struggling backend is never hit by a burst of identical
  queries. In both cases the last good data (kept for 10 minutes) is shown
  with a "could not be refreshed" warning. Use a shared cache backend
  (memcached, as in DevStack) so the cache and lock are shared between
  Horizon processes.
* **Range vectors through Aetos.** Aetos only exposes the instant query
  endpoint, so trends are fetched with subqueries (``expr[range:step]``) and
  the matrix response is decoded by Grian, because
  ``PrometheusAPIClient.query`` only parses instant vectors.


Troubleshooting
===============

**"No compute host metrics available"**
   Check that ``compute_monitors = cpu.virt_driver`` is set on the compute
   nodes, that ``nova-compute`` was restarted, and that the Ceilometer
   notification agent is running. In Prometheus,
   ``ceilometer_compute_node_cpu_percent`` should have one series per host.

**A host is stale**
   The host stopped sending ``compute.metrics.update`` notifications. Check
   ``nova-compute`` on the host and its connection to the message bus.

**No compute host is reporting**
   Every host stopped at the same time, which points at the telemetry
   pipeline: check the Ceilometer notification agent, sg-core and
   Prometheus.
