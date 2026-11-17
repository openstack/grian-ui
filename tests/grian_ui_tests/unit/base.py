#
# SPDX-License-Identifier: Apache-2.0

import django

from django import test


# Initialise the (empty) app registry so runtime gettext calls work in
# unit tests, which run without horizon.
django.setup()


class TestCase(test.SimpleTestCase):
    pass
