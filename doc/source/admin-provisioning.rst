======================
Provisioning Dashboard
======================

The Provisioning dashboard shows cloud administrators whether new instances
are getting slower to boot, on which compute hosts, and which builds failed.
It is accessible under **Admin > Telemetry > Provisioning** and requires the
``openstack.roles.admin`` permission.

Rising boot times are often an early sign of trouble with images (the Image
service or a host's image cache), scheduling or a busy host. Every panel
renders a bounded amount of data (summary numbers, at most 10 hosts, 10
slowest boots, 8 failed builds and 2,000 chart points) whatever the number
of boots or hosts.

Boot times come from `Ceilometer <https://docs.openstack.org/ceilometer/latest/>`_,
are stored in Prometheus via `sg-core <https://github.com/infrawatch/sg-core>`_
and are queried through the `Aetos <https://opendev.org/openstack/aetos>`_
observability proxy (``service_type: metric-storage``). Failed builds,
flavors and project names come from the Compute and Identity APIs.

This page covers requirements, data sources and configuration. For a guide
to using the dashboard, see :doc:`user-provisioning`.


Requirements
============

* The Ceilometer notification agent must receive Nova's
  ``compute.instance.create.end`` notification (unversioned notifications,
  Nova's default ``[notifications] notification_format``). Ceilometer turns
  it into the ``compute.instance.booting.time`` meter; no polling is
  involved.
* sg-core, Prometheus and Aetos as for the other Telemetry panels.
* The 14 day range needs a Prometheus retention of at least 14 days
  (``--storage.tsdb.retention.time``, 15 days by default). With a shorter
  retention the range simply shows fewer boots.
* The administrator's token must be allowed to list servers of all projects
  (``os_compute_api:servers:detail:get_all_tenants``) and projects
  (``identity:list_projects``), as the admin role is by default.


Metrics
=======

.. list-table::
   :header-rows: 1
   :widths: 45 15 40

   * - Prometheus metric
     - Unit
     - Used for
   * - ``ceilometer_compute_instance_booting_time``
     - s
     - Every number, chart and table except the failed builds

The value is the time from the create request (the instance's
``created_at``) until the instance is running (``launched_at``): scheduling,
image download, networking and the hypervisor's spawn. It is sent once per
instance, when its build ends; rebuilds do not send it. A failed build
never sends it.

Labels used: ``resource`` (instance UUID), ``resource_name`` (instance
name), ``project`` (project ID) and ``vm_instance``. Unlike the other
instance metrics, whose ``vm_instance`` is a hashed host ID, this metric's
``vm_instance`` is the plain **nova-compute host name** (``openstack compute
service list``). It can differ from the hypervisor hostname used by the
Compute Host Health dashboard (for example ``compute-01`` versus
``compute-01.example.com``).

Counting boots
--------------

sg-core keeps exposing the last value for a few minutes, so Prometheus
stores 10 to 20 identical samples per boot. The dashboard therefore counts
one boot per instance, never per sample, with two queries per range,
whatever the number of boots::

   max by (resource, resource_name, project, vm_instance)
     (last_over_time(ceilometer_compute_instance_booting_time[7d]))

   min by (resource)
     (min_over_time(timestamp(ceilometer_compute_instance_booting_time)[7d:5m]))

The first gives each boot's duration, the second when it was first seen,
to one step of the range (24h: 1 minute, 7d: 5 minutes, 14d: 10 minutes).
Grouping by these labels also counts a boot exported twice (several
notification agents or sg-core replicas) once.

Both queries return one series per boot of the range, so their size grows
with the number of boots: a cloud booting 1,000 instances a day returns
about 14,000 series for the 14 day range. The result is summarized once per
range and refresh and shared by every administrator (see `Caching`_).


Compute and Identity API calls
==============================

Every call reads a snapshot shared by the whole region (see `Caching`_):
page refreshes never call the APIs themselves, and the number of calls does
not grow with the number of administrators watching the page.

.. list-table::
   :header-rows: 1
   :widths: 40 35 25

   * - Call
     - Used for
     - How often (per region)
   * - ``GET /servers/detail?all_tenants=1&status=ERROR&changes-since=<range start>``
       (``limit=100``, newest first, microversion 2.47)
     - Failed builds: instances in ERROR that never launched and were
       created in the range. Microversion 2.47 includes the flavor name.
     - Once per range viewed, every 5 minutes
   * - ``GET /servers/<id>`` (microversion 2.47)
     - The flavor of each of the 10 slowest boots. A deleted instance shows
       a dash.
     - Once per instance, kept for an hour
   * - ``GET /os-hypervisors/detail`` (pages of 1,000)
     - Links from host names to Compute Host Health (see `Host links`_).
       Only read when that panel is enabled.
     - Every 10 minutes
   * - Identity project list
     - Project names instead of IDs.
     - Every 10 minutes

The dashboard never lists every server. **Deleted instances are not
counted** as failed builds, even when their build failed: an administrator
who deletes the instance also removes it from this page. At most the newest
100 ERROR instances are read per range; the card then shows "100+".

Flavor names need microversion 2.47. Horizon's public Compute client
helpers stop at 2.42, so the dashboard uses Horizon's internal client
factory (``openstack_dashboard.api._nova.novaclient``) with that version.


Host links
==========

The boot time metric names the nova-compute **service host**; Compute Host
Health names **hypervisors**. When the Compute Host Health panel is enabled,
the hypervisor list (``hypervisor_hostname`` and ``service.host``) maps each
service host to its hypervisor, and host names link to
``<Compute Host Health>/?host=<hypervisor hostname>``. A service host with
no hypervisor, or with several (for example Ironic), is shown without a
link, as are all hosts when the panel is not enabled or the hypervisor list
cannot be read.


Host status
===========

Hosts are always judged on the **last 24 hours**, whatever the selected
range: a host that turned slow recently would otherwise hide behind a week
of normal boots.

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Status
     - Rule (defaults)
   * - Slow
     - At least 3 boots in 24 hours, and a 24 hour median at least 2× the
       cloud's 24 hour median
   * - Slower
     - The same, at least 1.5×
   * - Normal
     - At least 3 boots in 24 hours, below 1.5×
   * - Too few boots
     - Fewer than 3 boots in 24 hours: not judged

On a cloud with a single compute host the ratio is always 1.0.


Summary cards
=============

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Card
     - Meaning and colours
   * - Instances booted
     - Boots in the range, and the number of hosts they ran on.
   * - Median boot time
     - Median of the range. Yellow above 30 s, otherwise green.
   * - 95th percentile / Slowest boot
     - The 95th percentile, or the slowest boot when the range has fewer
       than 20 boots. Red with a slow host, yellow with a slower host.
   * - Failed builds
     - Failed builds in the range, as a percentage of create requests
       (failed builds + boots). Yellow with any failure, red above 5%.
       Grey ("?") when the Compute API cannot be reached.


Configuration
=============

Thresholds can be overridden per key in ``local_settings``:

.. code-block:: python

   GRIAN_PLUGIN = {
       "datasource": "prometheus",
       "provisioning_thresholds": {
           "host_ratio_warning": 1.5,  # "Slower": host / cloud 24 h median
           "host_ratio_critical": 2.0,  # "Slow"
           "host_min_boots": 3,  # boots in 24 h before a host is judged
           "median_seconds_warning": 30,  # median card turns yellow
           "failed_percent_critical": 5,  # failed builds card turns red
       },
   }

Values must be numbers of at least 0, ``host_ratio_warning`` must not be
greater than ``host_ratio_critical``, and ``host_min_boots`` must be a whole
number of at least 1. An invalid value is shown at the top of the page.


Caching
=======

The partials refresh every 5 minutes. Each source is a snapshot shared
through the Horizon cache by every section and administrator of the region,
computed once when it expires, with a lock so the partials of one refresh
query only once:

* the summary of the boots of each range (Prometheus): every refresh;
* the failed builds of each range (Compute API): every 5 minutes;
* the hypervisor list (Compute API): every 10 minutes;
* the flavor of each slow instance (Compute API): once per instance, for an
  hour;
* the project names (Identity API): every 10 minutes.

Each section says when its data was fetched ("Metrics from …", "Compute API
data from … (refreshed every 5 minutes)", "Hypervisor list from …").
Times are sent as epoch seconds and shown in the browser's time zone, like
the other Telemetry panels (the time alone for today, with the date
otherwise); without JavaScript the UTC time is shown. Data
older than its refresh interval plus one page refresh is flagged as possibly
out of date; when a refresh fails, the last good copy is shown with a
warning. A section whose optional source (project names, flavors,
hypervisors, failed builds on the cards) fails still renders, with a note.

A shared cache such as memcached is needed for this; see
``grian_ui.cache``. The cached summary is bounded (at most 2,000 chart
points), whatever the number of boots.


Troubleshooting
===============

**"No instance booted"** with instances being created
   Check that the metric exists::

      openstack metric query \
          'count(last_over_time(ceilometer_compute_instance_booting_time[1d]))'

   If not, check that the Ceilometer notification agent receives Nova's
   notifications (``compute.instance.create.end``).

**Failed builds card shows "?"**
   The Compute API call failed; see the Horizon log.

**A host shows "too few boots in 24h"**
   It booted fewer than ``host_min_boots`` instances in the last 24 hours.
   This is normal on quiet clouds.
