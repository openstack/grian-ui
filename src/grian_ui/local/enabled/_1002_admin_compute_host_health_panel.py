# The name of the panel to be added to HORIZON_CONFIG. Required.
PANEL = "compute_host_health"

# The name of the dashboard the PANEL associated with. Required.
PANEL_DASHBOARD = "admin"

# The name of the panel group the PANEL is associated with.
PANEL_GROUP = "telemetry"

# Python panel class of the PANEL to be added.
ADD_PANEL = "grian_ui.content.compute_host_health.panel.ComputeHostHealth"

# The grian_ui app is added by _1000_admin_telemetry_panel_group.py.
