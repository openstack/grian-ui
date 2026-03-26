#
# SPDX-License-Identifier: Apache-2.0

# Grian UI configuration
# datasource options: 'fake', 'prometheus'
# Note: 'prometheus' datasource uses the Aetos pattern with authenticated
# Keystone session (following Watcher and CloudKitty). All Prometheus access
# goes through Aetos - no direct host:port specification.
GRIAN_PLUGIN = {"datasource": "prometheus"}
