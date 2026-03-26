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
    ``local/enabled`` directory of your Horizon installation.

2.  **Configure settings**

    Copy the settings file from ``src/grian_ui/local/local_settings.d``
    to the ``local/local_settings.d`` directory of your Horizon
    installation.

3.  **Install python-observabilityclient**

    Install the latest ``python-observabilityclient`` in your environment.
    Follow the instructions at
    https://github.com/openstack/python-observabilityclient.

After completing these steps, restart your web server.
