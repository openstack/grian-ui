# The name of the panel to be added to HORIZON_CONFIG. Required.
PANEL = "volume_metrics"

# The name of the dashboard the PANEL associated with. Required.
PANEL_DASHBOARD = "admin"

# The name of the panel group the PANEL is associated with.
PANEL_GROUP = "telemetry"

# Python panel class of the PANEL to be added.
ADD_PANEL = "grian_ui.content.volume_metrics.panel.VolumeMetrics"

# A list of applications to be prepended to INSTALLED_APPS
ADD_INSTALLED_APPS = ["grian_ui"]
