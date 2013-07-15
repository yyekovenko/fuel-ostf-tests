
from fuel_health.test import attr
from fuel_health.tests.smoke import base


""" Test module contains tests for flavor creation/deletion. """


class FlavorsAdminTest(base.BaseComputeAdminTest):
    """Tests for flavor creation that require admin privileges."""

    _interface = 'json'

    @attr(type=["fuel", "smoke"])
    def test_create_flavor(self):
        """Test low requirements flavor can be created."""
        resp, flavor = self.create_flavor(ram=255,
                                          disk=1)

        self.verify_response_status(
            resp.status, appl="Nova")
        self.verify_response_body_value(
            flavor['disk'], 1,
            msg="Disk size is not the same as requested.")
        self.verify_response_body(
            flavor, 'id',
            msg="Flavor was not created properly."
                "Please, check Nova.")
