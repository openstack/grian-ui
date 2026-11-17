==========================================
Using the Compute Host Health dashboard
==========================================

The Compute Host Health dashboard answers one question for cloud
administrators: *are my compute hosts healthy right now, and if not, which
ones and why?*

It shows how busy the CPUs of your hypervisors are, which hosts are
overloaded or waiting on storage, and which hosts stopped reporting. The page
stays the same size and speed whether your cloud has 10 or 2,000 compute
hosts.

Open it from **Admin > Telemetry > Compute Host Health**. You need the admin
role.

Hosts are identified by their **hypervisor hostname**, as shown by
``openstack hypervisor list`` and in **Admin > Compute > Hypervisors**. It
can differ from the ``nova-compute`` service host shown by
``openstack compute service list`` (for example ``compute-01`` versus
``compute-01.example.com``).

For how the dashboard is configured and where the data comes from, see
:doc:`admin-compute-host-health`.

Reading the page
================

Read the page from top to bottom:

#. The **summary cards** give the overall verdict.
#. The **attention list** names the hosts that need you, and why.
#. The **charts** show how the fleet behaves over time and how load is
   spread.
#. The **top hosts** table and **host lookup** let you dig into individual
   hosts.

The page refreshes itself every 30 seconds while its browser tab is visible.
Background tabs pause and catch up when you return. The time of the data is
shown under the cards ("Data from ...").


Time range
==========

The **1h / 6h / 24h / 7d** buttons choose the period shown by the trend
charts: the fleet chart, the trend column of the top hosts table and the
host lookup chart.

The status of a host does **not** depend on the selected range. It is always
based on the last 15 minutes, so changing the range never makes a host look
better or worse.

Use **1h** to see whether a problem is happening right now, and **24h** or
**7d** to tell a one-off spike from a daily pattern or a slow drift.

Gaps in the charts mean that no data was received at that time. A gap is not
the same as an idle host.


Summary cards
=============

**Hosts reporting**
   How many compute hosts are sending metrics, out of the hosts seen in the
   last 24 hours, for example ``493 / 501``. The card turns yellow when some
   hosts are stale; see `Stale hosts`_.

**Fleet CPU: median / p95**
   How busy a typical host is (median) and how busy the busiest 5% of hosts
   are (95th percentile), averaged over the last 15 minutes.

   * A high **median** means the whole cloud is busy: a capacity question.
   * A low median with a high **p95** means the load is unevenly spread: a
     placement question.

**Hosts needing attention**
   The number of hosts that are critical, warning, stale or unknown right
   now. Green with ``0`` means there is nothing to do.

**Highest I/O wait**
   The highest I/O wait in the fleet and the host it was measured on. I/O
   wait is CPU time spent waiting for storage. A high value usually points at
   local disks, the storage backend (for example Ceph or NFS) or the storage
   network of that host.


Host status
===========

Each host gets a status, shown as a coloured label with an icon and a word:

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Status
     - Meaning (default thresholds, 15 minute averages)
   * - Critical
     - CPU usage at or above 90%, or I/O wait at or above 20%.
   * - Warning
     - CPU usage at or above 75%, or I/O wait at or above 10%.
   * - Stale
     - The host reported in the last 24 hours but not in the last
       10 minutes.
   * - Unknown
     - The host reports, but without a usable CPU value.
   * - Healthy
     - None of the above.

The rules are repeated at the bottom of the page. Your cloud may use
different thresholds; the page always shows the ones in use.


Attention list
==============

When at least one host needs attention, a red or yellow box under the cards
lists the **10 worst hosts**, critical first, then warning, then stale and
unknown, with the reason, for example ``CPU 93.5%`` or ``I/O wait 24%``.
The heading always shows the total counts, even when more hosts are affected
than are listed.

Click a host name to open it in `Looking up a host`_.

Messages
--------

**No compute host is reporting** (above the cards)
   Every known host stopped reporting. When several hosts stopped within a
   few minutes of each other, the message says that the telemetry pipeline
   (the Ceilometer notification agent, sg-core or Prometheus) is the most
   likely cause. Check those services before investigating the hosts.

**Telemetry data could not be refreshed** (under the attention list)
   The monitoring backend did not answer. The page keeps showing the last
   data it received, with its time, for up to 10 minutes; after that the
   sections show an error instead. Treat the data as possibly out of date
   until the message goes away. Other sections show a short "Could not be
   refreshed" note in the same situation.

**Error loading telemetry data** (in place of a section)
   The full message reads "Error loading telemetry data. See the Horizon
   logs for details." That section could not load its data at all. Ask your
   operator if it persists.


Fleet CPU usage over time
=========================

This chart shows three lines for the selected range:

* **Median**: a typical host.
* **95th percentile**: the busiest 5% of hosts.
* **Busiest host (max)**: the single busiest host at each point in time.

Dashed lines mark the warning and critical thresholds.

How to read it:

* **The median rises over days or weeks.** The cloud is running out of
  compute capacity: plan more hosts or review CPU overcommit.
* **The 95th percentile is near the thresholds but the median is low.** A
  subset of hosts is overloaded: consider live-migrating instances away from
  them, or review the scheduler configuration.
* **The busiest host stays near 100%.** At least one host is saturated most
  of the time; find it in `Top hosts`_.
* **All lines jump at the same moment.** Something happened across the
  fleet, such as a batch workload, a migration wave or an upgrade.


Hosts by CPU usage
==================

This chart shows how many hosts fall into each CPU usage band, by default
0–25%, 25–50%, 50–75%, 75–90% and 90% or more. The band colours match the
host status colours.

How to read it:

* **Most hosts in the low bands and a few at 90% or more.** The load is
  unevenly placed.
* **The bulk of hosts moves to the right over time.** Capacity is getting
  tight.
* **A large group in the lowest band.** Candidates for consolidation, for
  example to free hosts for maintenance.

Click a bar, or one of the band buttons under the chart, to list the busiest
hosts of that band in the top hosts table.


Top hosts
=========

The table lists at most 10 hosts. Choose the list with the buttons at the
top right:

**Busiest CPU** (default)
   The hosts with the highest CPU usage over the last 15 minutes. These are
   the first candidates for moving instances away or for a closer look.

**Highest I/O wait**
   The hosts waiting most on storage.

**Stale**
   The hosts that stopped reporting, the oldest first. Start here when hosts
   go missing.

**CPU** *band*
   Appears when you pick a band in `Hosts by CPU usage`_.

Each row shows:

* **CPU usage (now)**: the latest CPU usage, split into *user*, *kernel*
  and *I/O wait*.

  - High *user* time is the normal workload of the instances.
  - High *kernel* time often means virtualization or networking overhead on
    the host, for example virtual switching or interrupt load.
  - High *I/O wait* means the CPUs are waiting for storage.

* **Avg (15m)** and **I/O wait (15m)**: the values the status is based on.
* **Frequency**: the CPU clock speed at the last report. Idle hosts normally
  slow their CPUs down, so this only matters under load: a host running well
  below the speed of similar hosts *with a comparable load* may be throttled
  by heat or a power-saving profile.
* **Trend**: CPU usage over the selected range, to tell new problems from
  ongoing ones.
* **Last seen**: approximately when data was last received from the host.
  It can lag by a few minutes: a host that just stopped still shows a recent
  time for about three minutes.

Click a host name to open it in `Looking up a host`_. The footer tells you
how many hosts match the selected list; use host lookup for hosts that are
not shown.


Looking up a host
=================

Use **Look up a host** to see any single compute host:

#. Click the box: it suggests the hosts needing attention first.
#. Start typing: it suggests up to 20 matching host names, names starting
   with what you typed first. Hosts that are not healthy show their status
   next to the name.
#. Pick a suggestion to show the host, or type the exact hypervisor hostname
   (it is case-sensitive) and press **Enter** or **Show**.

You can also click any host name elsewhere on the page.

The result shows the host's status and the reason, its CPU breakdown, CPU
frequency, when it was last seen, and a chart of its CPU usage over the
selected range **compared with the fleet median**:

* far above the median: the cause is on this host (its workload or
  hardware);
* following the median: the whole fleet is affected.

The result refreshes with the rest of the page. For a stale host, the result
tells you when the host was last seen.

To find the host behind a slow instance, look up the hypervisor hostname of
the instance first, for example::

   openstack server show <instance> -c OS-EXT-SRV-ATTR:hypervisor_hostname


Common tasks
============

Daily check
   If no card is orange or red and there is no attention list, there is
   nothing to do. Otherwise, read the attention list.

"Instances are slow"
   Check whether the problem is fleet-wide (the median card and the fleet
   chart) or local (the attention list). Then open **Busiest CPU** or
   **Highest I/O wait** in the top hosts table, and look up the host of the
   affected instances to compare it with the fleet.

Hosts went missing
   If the page reports that the telemetry pipeline is the likely cause,
   check the telemetry services first. Otherwise open the **Stale** list,
   oldest first, and check the hosts with::

      openstack hypervisor list
      openstack compute service list --service nova-compute

   The hypervisor list uses the same names as the dashboard; the service
   list shows whether ``nova-compute`` is up on each service host.

Before maintenance
   Look up the host to confirm its load, and watch it drop while you
   live-migrate its instances away.

Capacity planning
   Select **7d** and watch the median line of the fleet chart and the
   spread of the CPU bands. A rising median calls for more capacity; a few
   hot hosts next to many idle ones call for rebalancing.


Stale hosts
===========

A host becomes stale when no CPU data has been received from it for
10 minutes. Because of how the data is collected, it takes about 13 to
14 minutes after a host stops before it is shown as stale.

Common causes:

* the host is down or unreachable;
* ``nova-compute`` is stopped or cannot reach the message bus;
* the telemetry pipeline is down (several hosts go stale at once).

Hosts that have not reported for more than 24 hours are no longer shown.
Use ``openstack hypervisor list`` (its *State* column) and
``openstack compute service list`` to find hosts and compute services that
have been down for longer.


Limitations
===========

* The dashboard shows **CPU** health. It does not show memory or disk usage
  of the hosts, nor how much capacity is allocated to instances.
* It does not list the instances running on a host; use
  **Admin > Compute > Hypervisors** or the command line.
* It is an overview, not an alarm: a dead host appears as stale after about
  13 to 14 minutes. Use your alerting system for paging.
* It only shows hosts whose CPU monitor is enabled (see
  :doc:`admin-compute-host-health`). If the page reports "No compute host
  metrics available", ask your operator to enable it.
