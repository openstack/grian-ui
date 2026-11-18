#
# SPDX-License-Identifier: Apache-2.0

# our unit tests should not need to depend on horizon.

import os


DEBUG = True

GRIAN_PLUGIN = {
    "datasource": "fake",
}

# Render the panel templates in unit tests (without horizon).
_TEMPLATE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "src", "grian_ui", "templates"
)
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.abspath(_TEMPLATE_DIR)],
    }
]
ROOT_URLCONF = "grian_ui_tests.unit.urls"
