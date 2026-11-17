=========================================
Testing the Compute Host Health panel
=========================================

.. note::
    These are development instructions. See
    ``doc/source/admin-compute-host-health.rst`` for the panel
    documentation.

Prerequisites
=============

1.  **Enable the Nova CPU monitor**

    On the compute nodes, set ``compute_monitors = cpu.virt_driver`` in the
    ``[DEFAULT]`` section of ``nova.conf`` and restart ``nova-compute``.
    Ceilometer (notification agent), sg-core, Prometheus and Aetos must be
    deployed (see the DevStack sample ``devstack/local.conf-all-in-one``).

2.  **Enable the panels**

    Copy the files under ``src/grian_ui/local/enabled`` to the
    ``local/enabled`` directory of your Horizon installation. The panel
    group file ``_1000_admin_telemetry_panel_group.py`` is required: it adds
    the ``grian_ui`` application.

3.  **Configure settings**

    Copy the settings file from ``src/grian_ui/local/local_settings.d`` to
    the ``local/local_settings.d`` directory of your Horizon installation.
    Optionally override the status thresholds with
    ``GRIAN_PLUGIN['compute_host_thresholds']``.

4.  **Fetch the vendored JavaScript**

    Run ``src/grian_ui/static/vendor/get_vendor.sh`` (Chart.js and htmx),
    then ``collectstatic``.

After completing these steps, restart your web server.

Checking the data
=================

Each compute host should have one ``ceilometer_compute_node_cpu_percent``
series in Prometheus, labelled ``vm_instance="compute.<host>"``::

    openstack metric query 'count(ceilometer_compute_node_cpu_percent)'

With the ``fake`` datasource (``GRIAN_PLUGIN = {"datasource": "fake"}``) the
panel shows 24 sample hosts, two of them critical, two warning and two
stale.
