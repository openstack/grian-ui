=================================
Testing the Instance Health panel
=================================

.. note::
    These are development instructions. See
    ``doc/source/admin-instance-health.rst`` for the panel documentation.

Prerequisites
=============

1.  **Deploy the telemetry pipeline**

    Ceilometer (compute agent), sg-core, Prometheus and Aetos must be
    deployed (see the DevStack sample ``devstack/local.conf-all-in-one``).

2.  **Enable the panels**

    Copy the files under ``src/grian_ui/local/enabled`` to the
    ``local/enabled`` directory of your Horizon installation. The panel
    group file ``_1000_admin_telemetry_panel_group.py`` is required: it adds
    the ``grian_ui`` application.

3.  **Configure settings**

    Copy the settings file from ``src/grian_ui/local/local_settings.d`` to
    the ``local/local_settings.d`` directory of your Horizon installation.
    Optionally override the thresholds with
    ``GRIAN_PLUGIN['instance_health_thresholds']``.

4.  **Fetch the vendored JavaScript**

    Run ``src/grian_ui/static/vendor/get_vendor.sh`` (Chart.js and htmx),
    then ``collectstatic``.

After completing these steps, restart your web server.

Producing symptoms
==================

On a small cloud, real symptoms can be produced with CirrOS instances and
user data:

* CPU contention: boot more busy vCPUs than the host has, e.g. two
  instances with 4 vCPUs each on an 8-core host, running
  ``for i in 1 2 3 4; do (while :; do :; done) & done``.
* Slow disk: a loop of ``dd ... conv=fsync`` on a busy host.
* State problems: a flavor too large for any host (``ERROR``).
* No telemetry: stop ``ceilometer-polling`` on the compute host.

With the ``fake`` datasource (``GRIAN_PLUGIN = {"datasource": "fake"}``) the
panel shows 18 sample instances with every kind of symptom.
