==================================
Testing the Provisioning panel
==================================

.. note::
    These are development instructions. See
    ``doc/source/admin-provisioning.rst`` for the panel documentation.

Prerequisites
=============

1.  **Telemetry**

    Ceilometer (notification agent), sg-core, Prometheus and Aetos must be
    deployed (see the DevStack sample ``devstack/local.conf-all-in-one``).
    Boot times are sent by Nova when an instance build ends: create an
    instance to get a sample.

2.  **Enable the panels**

    Copy the files under ``src/grian_ui/local/enabled`` to the
    ``local/enabled`` directory of your Horizon installation. The panel
    group file ``_1000_admin_telemetry_panel_group.py`` is required: it adds
    the ``grian_ui`` application.

3.  **Configure settings**

    Copy the settings file from ``src/grian_ui/local/local_settings.d`` to
    the ``local/local_settings.d`` directory of your Horizon installation.
    Optionally override the thresholds with
    ``GRIAN_PLUGIN['provisioning_thresholds']``.

4.  **Fetch the vendored JavaScript**

    Run ``src/grian_ui/static/vendor/get_vendor.sh`` (Chart.js and htmx),
    then ``collectstatic``.

After completing these steps, restart your web server.

Checking the data
=================

Each instance booted in the last day should have one series::

    openstack metric query \
        'max by (resource_name, vm_instance) (last_over_time(ceilometer_compute_instance_booting_time[1d]))'

Failed builds are instances in ERROR that never launched::

    openstack server list --all-projects --status ERROR --long

Host names link to Compute Host Health when that panel is enabled, through
the hypervisor list (service host -> hypervisor hostname)::

    openstack hypervisor list --long

With the ``fake`` datasource (``GRIAN_PLUGIN = {"datasource": "fake"}``) the
panel shows a 40 host sample cloud with about 3,000 boots over 14 days, one
slow host (``compute-017``, slow for the last two days), seven failed
builds and hypervisors for every host but ``compute-040`` (shown without a
link).
