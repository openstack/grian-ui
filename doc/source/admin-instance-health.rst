=========================
Instance Health Dashboard
=========================

The Instance Health dashboard answers one question for cloud administrators:
*which instances are in trouble right now?* It is accessible under
**Admin > Telemetry > Instance Health** and requires the
``openstack.roles.admin`` permission.

It joins two sources:

* the **Compute API** (Nova), for every instance of every project, its
  status, power state, task and host. Ceilometer stops reporting an
  instance when it stops running, so stopped, errored and stuck instances
  are only known to Nova;
* the **Ceilometer instance meters**, stored in Prometheus via
  `sg-core <https://github.com/infrawatch/sg-core>`_ and queried through the
  `Aetos <https://opendev.org/openstack/aetos>`_ observability proxy
  (``service_type: metric-storage``), for the performance symptoms.

Project names come from Keystone. All calls use the administrator's token.

The browser receives a bounded amount of data whatever the size of the
cloud (summary numbers, a fixed set of states and symptoms, and at most 25
instances), and the four symptom queries are filtered by threshold in
Prometheus, so they return only the instances with a problem. The
dashboard still reads every instance from Nova (every 2 minutes) and one
series per running instance from Prometheus; see `Scale and caching`_.


Using the dashboard
===================

Summary cards
   Instances needing attention (critical and warning), instances in all
   projects, running instances that report metrics, and the compute hosts
   running an instance that needs attention.

Instances by state
   Every instance, by state, from the Compute API. Click *Shut off*,
   *Paused*, *Suspended*, *Shelved* or *Active (running)* to list those
   instances (from the Compute API, by name, at most 25; the search box
   matches part of a name, or a full instance ID), or *Error* and *Active,
   not running* to show those problems in the list. With a severity
   selected, a state lists its instances needing attention instead.

Symptom tiles
   The number of instances with each symptom. Click a tile to filter the
   list; click it again to clear the filter.

Instances needing attention
   The instances with at least one issue, worst first: critical before
   warning, lifecycle problems before metric symptoms, then the instances
   with more issues, then those furthest past their threshold. Filter by
   name or instance ID, project and severity. The sparkline shows the worst
   metric symptom over the selected range.

   Click a row for the trend of each symptom with its thresholds, the Nova
   fault of errored instances, and links to the instance details and the
   console log. Open rows stay open across refreshes.

The page refreshes every 30 seconds while it is visible. The metric
symptoms are as fresh; the instance list from Nova is refreshed every
2 minutes, so a state change (an instance going into error, getting stuck
in a task or being shut off) can take up to 2 minutes to show. The times of
both are shown under the symptom tiles. **How this page is calculated**, at
the bottom, lists the measure behind every part of the page.


Symptoms
========

A symptom is a **warning** above its warning threshold and **critical**
above its critical threshold.

.. list-table::
   :header-rows: 1
   :widths: 18 42 20 20

   * - Symptom
     - Measure
     - Warning above
     - Critical above
   * - CPU contention
     - Share of vCPU time spent waiting for a host CPU:
       ``rate(cpu.delay)`` ÷ ``vcpus``
     - 5 %
     - 20 %
   * - Memory pressure
     - Guest swap in + out: ``rate(memory.swap.in + memory.swap.out)``
     - 0 MiB/s
     - 10 MiB/s
   * - Slow disk
     - Average time per request on the instance's worst disk:
       ``rate(disk.device.*.latency)`` ÷ ``rate(disk.device.*.requests)``
     - 20 ms
     - 100 ms
   * - Network drops/errors
     - Dropped and failed packets on all interfaces:
       ``rate(network.*.packets.drop + network.*.packets.error)``
     - 0 pkt/s
     - 10 pkt/s
   * - State problems
     - Nova status and power state
     - Active but not running; in the same task for more than 30 minutes
     - Error; crashed
   * - No telemetry
     - Running in Nova, started (or last updated in Nova, e.g. renamed or
       migrated) more than 10 minutes ago, but no ``cpu`` sample in the
       last 10 minutes (the rate window instead, when longer)
     - always
     -

Shut off, paused, suspended and shelved instances are never flagged: they
may be stopped on purpose.

Memory pressure needs the libvirt memory balloon statistics, which only
guests with a balloon driver and swap report.


Metrics
=======

.. list-table::
   :header-rows: 1
   :widths: 55 15 30

   * - Prometheus metric
     - Unit
     - Used for
   * - ``ceilometer_cpu``
     - ns
     - Reporting instances, last sample
   * - ``ceilometer_cpu_delay``
     - ns
     - CPU contention
   * - ``ceilometer_vcpus``
     - vCPU
     - CPU contention
   * - ``ceilometer_memory_swap_in``, ``ceilometer_memory_swap_out``
     - MiB
     - Memory pressure
   * - ``ceilometer_disk_device_{read,write}_latency``,
       ``ceilometer_disk_device_{read,write}_requests``
     - ns, requests
     - Slow disk
   * - ``ceilometer_network_{incoming,outgoing}_packets_{drop,error}``
     - packets
     - Network drops/errors

These meters are polled by the Ceilometer compute agent. The instance is
identified by the UUID in the ``resource`` label (``<uuid>`` for instance
meters, ``<uuid>-<disk>`` for disks, ``<libvirt name>-<uuid>-<vnic>`` for
interfaces). The ``vm_instance`` label of instance meters is a hash of the
project and host, so compute hosts are taken from Nova
(``OS-EXT-SRV-ATTR:hypervisor_hostname``, the name the Compute Host Health
dashboard uses).

To check that the meters are flowing::

   openstack metric query 'count(last_over_time(ceilometer_cpu_delay[10m]))'


Configuration
=============

Override any threshold with ``GRIAN_PLUGIN['instance_health_thresholds']``
in Horizon's ``local_settings``; the other keys keep their defaults:

.. code-block:: python

   GRIAN_PLUGIN = {
       "datasource": "prometheus",
       "instance_health_thresholds": {
           "cpu_wait_warning": 10,
           "disk_latency_critical": 200,
       },
   }

.. list-table::
   :header-rows: 1
   :widths: 40 20 40

   * - Key
     - Default
     - Unit
   * - ``cpu_wait_warning``, ``cpu_wait_critical``
     - 5, 20
     - percent of vCPU time
   * - ``swap_warning``, ``swap_critical``
     - 0, 10
     - MiB/s
   * - ``disk_latency_warning``, ``disk_latency_critical``
     - 20, 100
     - ms per request
   * - ``packet_loss_warning``, ``packet_loss_critical``
     - 0, 10
     - packets/s
   * - ``stuck_task_minutes``
     - 30
     - minutes

Rates are computed over ``GRIAN_PLUGIN['instance_health_rate_window']``
(default ``"10m"``, at most ``"6h"``). The window must hold at least two
samples: use at least twice the Ceilometer polling interval of the
``cpu.delay``, ``memory.swap.*``, ``disk.device.*`` and ``network.*``
meters (``polling.yaml``, 300 seconds by default). A rate window longer
than 10 minutes also lengthens the *No telemetry* window and its grace
period after an instance starts, so slower polling does not make healthy
instances look silent.

Invalid settings are reported at the top of the page.


Scale and caching
=================

The page joins two inputs, each computed once and shared:

* the **instance inventory**: one full listing of every instance of every
  project from Nova (``limit=-1``: one request per ``[api] max_limit``
  instances, 1000 by default, plus a last, empty page), kept for
  **2 minutes**;
* the **metrics**, kept for 25 seconds: one Prometheus query returning one
  series per running instance (the instances that reported a ``cpu``
  sample, used for *No telemetry* and the *Reporting metrics* card) and
  four Prometheus symptom queries, filtered by threshold.

Both are kept in the Horizon cache and shared by the page sections and by
every administrator of the region; only one request computes each at a
time, and the others wait for it (at most 5 seconds) or get the last copy,
or show "Still loading" and retry. This needs a cache shared by the Horizon
processes (memcached, Horizon's default). So while at least one
administrator has the page open, Nova is listed about every 2 minutes. On
very large clouds, check the cost of a full
``GET /servers/detail?all_tenants=1`` in your deployment. Each Horizon
process joins the two inputs into the *snapshot* once per pair (in memory,
not in the shared cache).

An inventory larger than the cache's item size limit
(``GRIAN_PLUGIN['cache_max_item_bytes']``, 1 MB by default, about 6000
instances) is kept per Horizon process instead, with a warning in the
log: each process then lists Nova every 2 minutes. The same happens to the
metrics above about 24000 reporting instances: each process then runs the
Prometheus queries every 25 seconds. Raise the limit (and memcached's
``-I`` option) to share them again.

On top of the snapshot, the instance list makes one query for the last
sample of the instances it shows and one per kind of symptom drawn in the
sparklines (at most four); an open row makes one query, and a state list
(e.g. *Shut off*) one Nova request per status, limited to 35 instances.

The snapshot keeps the 500 worst instances needing attention; the counts
stay exact. Use a shared cache such as memcached in production
(``CACHES`` in ``local_settings``).


Partial failures
================

* **Compute API unavailable**: the metric symptoms are still shown, with
  instance names and projects from the metric labels. States, hosts, state
  problems and *No telemetry* are unavailable.
* **Metrics unavailable**: the instance states and state problems are
  still shown; the metric symptoms are unavailable.
* **Keystone unavailable**: project IDs are shown instead of names.
* When a refresh fails, the last data received is shown with a warning
  ("Could not be refreshed"). When a refresh only takes long (data older
  than its lifetime plus 30 seconds, e.g. a slow Nova listing), the page
  says it is refreshing and the data may be a few minutes old. When the
  browser cannot reach Horizon, each section says that it could not be
  refreshed.

Error details are written to the Horizon log, not shown on the page.
