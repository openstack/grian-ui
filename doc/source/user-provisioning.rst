===================================
Using the Provisioning dashboard
===================================

The Provisioning dashboard answers one question for cloud administrators:
*are new instances getting slower to boot, and where?*

It shows how long instances take from the create request until they are
running, which compute hosts boot slower than the rest of the cloud, the
slowest boots and the builds that failed. The page stays the same size
whatever the number of boots.

Open it from **Admin > Telemetry > Provisioning**. You need the admin role.

Hosts are identified by their **nova-compute host name**, as shown by
``openstack compute service list``. When the Compute Host Health panel is
enabled, a host name links to that host in Compute Host Health (which uses
the hypervisor hostname, for example ``compute-01.example.com``).

For how the dashboard is configured and where the data comes from, see
:doc:`admin-provisioning`.


Reading the page
================

#. The **summary cards** give the overall verdict.
#. The **boot times** chart shows every boot over time.
#. The **By compute host** table names the hosts that boot slower than the
   cloud.
#. **Failed builds** lists the newest builds that never started.
#. **Slowest boots** lists the slowest boots of the range.

The page refreshes itself every 5 minutes while its browser tab is visible.
Each section says when its data was fetched: "Metrics from ..." for the
boot times, "Compute API data from ... (refreshed every 5 minutes)" for the
failed builds. All times on the page are in your browser's time zone: the
time alone for today, with the date for earlier days. "Refreshing: the
data shown may be out of date" means a refresh is late; "Could not be
refreshed" means it failed and the last data received is shown.


Time range
==========

The **24h / 7d / 14d** buttons choose the period of every panel. 14 days
needs Prometheus to keep 14 days of metrics.

The **status of a host** does not depend on the range: it is always based
on the last 24 hours, so a host that turned slow yesterday shows as slow
even if its week looks normal. Use **7d** or **14d** to see since when
boots are slower.


Summary cards
=============

**Instances booted**
   How many instances booted in the range, and on how many hosts.

**Median boot time**
   The typical boot. With few boots (fewer than 20) the card says the value
   is only indicative.

**95th percentile** or **Slowest boot**
   How slow the slowest boots are. The card turns red when a host is slow,
   and names it.

**Failed builds**
   Builds that failed in the range, as a share of all create requests. "?"
   means the Compute API could not be reached.


Boot times
==========

One dot per boot: when it happened and how many seconds it took. Boots on a
slow host are orange triangles; the dashed line is the median of the range.
Hover a dot to see the instance, its project and its host.

A cloud with many boots shows at most 2,000 dots: the boots of slow hosts are
always shown, the others are thinned evenly. The cards and tables always use
every boot.


By compute host
===============

The 10 hosts that boot slowest compared to the cloud, judged on the last
24 hours:

* **Boots, Median, Slowest:** over the selected range.
* **Last 24h median:** with the number of boots in brackets.
* **vs cloud, 24h:** the host's 24 hour median divided by the cloud's. 2.0×
  means twice as slow as a typical boot.
* **Status:** *Slow* (at least 2×), *Slower* (at least 1.5×), *Normal*, or
  *too few boots in 24h* (fewer than 3 boots: not judged).

Common causes of a slow host: its image cache (every boot downloads the
image), slow downloads from the Image service, or a busy host (check it in
Compute Host Health).


Failed builds
=============

The newest instances that went to ERROR without ever starting, with their
project, flavor, creation time and fault message. A failed build has no boot
time, so it never appears in the other panels.

**Deleted instances are not counted**, even if their build failed. At most
the newest 100 failed instances are read per range: the card then shows
"100+".

"No valid host was found" means the scheduler found no host with room for
the flavor. Repeated, it is a capacity signal.


Slowest boots
=============

The 10 slowest boots of the range, with their project, host, flavor and when
they were first seen. A dash in *Flavor* means the instance was deleted since
it booted.


Common tasks
============

**"Instances take ages to start"**
   Look at the third card and the **By compute host** table. One slow host
   points at that host (image cache, disk, load); the whole cloud being
   slower points at the Image service or the network.

**"My instance went to ERROR"**
   Find it in **Failed builds** and read its fault.

**Checking capacity**
   Several "No valid host" failures for the same flavor mean no host has
   room for it.
