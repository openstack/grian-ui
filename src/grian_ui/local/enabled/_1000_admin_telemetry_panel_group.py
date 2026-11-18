PANEL_GROUP = "telemetry"
PANEL_GROUP_NAME = "Telemetry"
PANEL_GROUP_DASHBOARD = "admin"

# Add the grian_ui app (templates, static files) once, here, because every
# grian-ui panel needs this panel group. Horizon does not de-duplicate
# ADD_INSTALLED_APPS across enabled files, and skips a file entirely when it
# sets DISABLED = True, so the app must not depend on a panel's file.
# Disable panels in their own files, not by disabling this file: that
# would drop the app while the panels stay registered.
#
# AUTO_DISCOVER_STATIC_FILES is intentionally not set: it would add every
# .js file under grian_ui/static (htmx, Chart.js) to every Horizon page.
# The panels load those scripts in their own templates.
ADD_INSTALLED_APPS = ["grian_ui"]
