================================
Testing the Volume Metrics panel
================================

.. note::
    These are development instructions. This content will be moved to the
    official documentation.

This document provides instructions on how to test the Volume Metrics panel.

Prerequisites
=============

1.  **Enable the panels**

    Copy the files under ``src/grian_ui/local/enabled`` to the
    ``local/enabled`` directory of your Horizon installation. The panel
    group file ``_1000_admin_telemetry_panel_group.py`` is required: it adds
    the ``grian_ui`` application. Always copy the files together.

2.  **Configure settings**

    Copy the settings file from ``src/grian_ui/local/local_settings.d``
    to the ``local/local_settings.d`` directory of your Horizon
    installation.

3.  **Install python-observabilityclient**

    Install the latest ``python-observabilityclient`` in your environment.
    Follow the instructions at
    https://github.com/openstack/python-observabilityclient.

After completing these steps, restart your web server.

Disabling a panel
=================

Set ``DISABLED = True`` in the panel's own enabled file (for example
``_1001_admin_volume_metrics_panel.py``). Do not disable the panel group file
``_1000_admin_telemetry_panel_group.py`` on its own: Horizon then skips it,
so the ``grian_ui`` application is not installed, but the panels are still
registered: they move to an "Other" group in the Admin dashboard and their
pages fail (template not found). To remove every Telemetry panel, disable
each panel file (the group file can stay).
