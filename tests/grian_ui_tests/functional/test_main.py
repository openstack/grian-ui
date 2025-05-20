#
# SPDX-License-Identifier: Apache-2.0

from grian_ui import main
from grian_ui_tests.unit import base


class MainTests(base.TestCase):
    def test_message(self):
        self.assertEqual("hi", main.message())
