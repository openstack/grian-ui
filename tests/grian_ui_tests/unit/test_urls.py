#
# SPDX-License-Identifier: Apache-2.0

from django.template.loader import get_template
from django.urls import reverse

from grian_ui_tests.unit import base


class TestInfrastructureTests(base.TestCase):
    """The unit tests can resolve panel URLs and load panel templates."""

    def test_panel_urls_use_horizon_namespaces(self):
        self.assertEqual(
            reverse("horizon:admin:volume_metrics:index"),
            "/dashboard/admin/volume_metrics/",
        )
        self.assertEqual(
            reverse("horizon:admin:volume_metrics:capacity_prediction"),
            "/dashboard/admin/volume_metrics/capacity-prediction/",
        )

    def test_panel_templates_are_found(self):
        template = get_template(
            "admin/volume_metrics/_capacity_prediction.html"
        )
        self.assertIsNotNone(template)
