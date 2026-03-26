========================
Volume Metrics Dashboard
========================

The Volume Metrics dashboard provides cloud administrators with a real-time
overview of Cinder block storage resources across the deployment. It is
accessible under **Admin > Telemetry > Volume Metrics** and requires the
``openstack.roles.admin`` permission.

All metrics originate from `Ceilometer <https://docs.openstack.org/ceilometer/latest/>`_
volume pollster data, stored in Prometheus via the
`sg-core <https://github.com/infrawatch/sg-core>`_ telemetry pipeline, and
queried through the `Aetos <https://opendev.org/openstack/aetos>`_
observability proxy (``service_type: metric-storage``).

Ceilometer meters follow a naming convention when exposed as Prometheus
metrics: dots are replaced with underscores and a ``ceilometer_`` prefix is
added (e.g. ``volume.size`` becomes ``ceilometer_volume_size``).


Panels
======

Summary Cards
-------------

Four metric cards displayed at the top of the dashboard, auto-refreshing
every **10 seconds**.

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Card
     - Ceilometer Meter
     - PromQL
   * - Total Storage (GiB)
     - ``volume.provider.pool.capacity.total``
     - ``sum(ceilometer_volume_provider_pool_capacity_total)``
   * - Allocated Storage (GiB)
     - ``volume.provider.pool.capacity.allocated``
     - ``sum(ceilometer_volume_provider_pool_capacity_allocated)``
   * - Total Volumes
     - ``volume.size``
     - ``count(ceilometer_volume_size)``
   * - Total Snapshots
     - ``volume.snapshot.size``
     - ``count(ceilometer_volume_snapshot_size)``

**Total Storage** and **Allocated Storage** aggregate the capacity reported
by the Cinder volume backend(s) (e.g. LVM, Ceph) across all storage pools.
**Total Volumes** and **Total Snapshots** count the number of distinct
Ceilometer samples, which corresponds to the number of active volumes and
snapshots in the deployment.


Volume and Snapshot Distribution (Doughnut Chart)
-------------------------------------------------

A Chart.js doughnut chart showing the proportion of volumes to snapshots.
Rendered on initial page load from the same ``count()`` queries used by the
summary cards.

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Segment
     - Ceilometer Meter
     - PromQL
   * - Volumes
     - ``volume.size``
     - ``count(ceilometer_volume_size)``
   * - Snapshots
     - ``volume.snapshot.size``
     - ``count(ceilometer_volume_snapshot_size)``

This chart gives a quick visual ratio of how storage objects are distributed
between volumes and snapshots.


Storage Pools
-------------

A per-pool breakdown with capacity bars and a detailed table. Rendered on
initial page load.

The dashboard first discovers all pools by querying the raw (un-aggregated)
capacity total metric, then fetches per-pool details by filtering on the
``resource`` label, which contains the Cinder pool name.

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Metric
     - Ceilometer Meter
     - PromQL
   * - Total capacity
     - ``volume.provider.pool.capacity.total``
     - ``ceilometer_volume_provider_pool_capacity_total``
   * - Allocated capacity
     - ``volume.provider.pool.capacity.allocated``
     - ``ceilometer_volume_provider_pool_capacity_allocated{resource="<pool>"}``
   * - Free capacity
     - ``volume.provider.pool.capacity.free``
     - ``ceilometer_volume_provider_pool_capacity_free{resource="<pool>"}``
   * - Provisioned capacity
     - ``volume.provider.pool.capacity.provisioned``
     - ``ceilometer_volume_provider_pool_capacity_provisioned{resource="<pool>"}``

Each pool is displayed with:

- A **capacity bar** showing the percentage of allocated vs. total capacity.
- A **detail row** with free and provisioned values.
- A **collapsible table** with all numeric values and a color-coded usage
  percentage label (green < 75%, yellow 75-89%, red >= 90%).

All values are in GiB, matching the units reported by Cinder's volume
backend statistics.


Capacity Forecast
-----------------

A prediction table that forecasts when each storage pool will run out of
free space, auto-refreshing every **60 seconds**.

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Metric
     - Ceilometer Meter
     - PromQL
   * - Current free capacity
     - ``volume.provider.pool.capacity.free``
     - ``ceilometer_volume_provider_pool_capacity_free{resource="<pool>"}``
   * - Predicted free in 30 days
     - ``volume.provider.pool.capacity.free``
     - ``predict_linear(ceilometer_volume_provider_pool_capacity_free{resource="<pool>"}[7d], 30 * 86400)``

The ``predict_linear()`` function uses linear regression over the last
**7 days** of free capacity samples to project the value **30 days** into
the future. If the predicted value is negative, the dashboard computes the
estimated number of days until the pool reaches zero free capacity using
linear interpolation::

    decline_per_day = (current_free - predicted_value) / 30
    days_until_full = current_free / decline_per_day

Status labels:

- **Full in N days** (red) — predicted free capacity drops below zero within
  30 days.
- **Declining** (yellow) — free capacity is decreasing but will not reach
  zero within 30 days.
- **Healthy** (green) — free capacity is stable or increasing.
- **Insufficient data** (grey) — not enough historical data for
  ``predict_linear()`` to produce a result (requires at least 2 data points
  over the 7-day window).

.. note::

   Predictions require at least 7 days of continuous Ceilometer data
   collection. On a fresh deployment, this panel will show "Insufficient
   data" until enough history accumulates.


Data Pipeline
=============

The metrics flow through the following pipeline::

    Cinder → Ceilometer pollster → sg-core (STF) → Prometheus → Aetos → Horizon

1. **Ceilometer** polls Cinder every 600 seconds (default) for volume and
   pool statistics.
2. **sg-core** receives Ceilometer samples over AMQP/TCP and writes them as
   Prometheus metrics.
3. **Prometheus** stores the time-series data and serves PromQL queries.
4. **Aetos** acts as a Keystone-authenticated proxy to Prometheus, registered
   in the service catalog as ``metric-storage``.
5. **Horizon (grian-ui)** queries Aetos using ``python-observabilityclient``
   and renders the results.
