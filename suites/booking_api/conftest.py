"""API regression suite: Restful-Booker (https://restful-booker.herokuapp.com), a public practice API.

The "smoke" tests check the happy path only. The full regression adds the negative and contract
tests a real release needs, and shows what a happy-path-only check would let through.
"""
import pytest

from booking import Api


@pytest.fixture
def api(step):
    return Api(step)


@pytest.fixture
def booking(api, step):
    """A fresh booking for tests that read or change one."""
    with step("Create a booking to work with", "Créer une réservation de test"):
        r = api.create()
        assert r.status_code in (200, 201), f"Could not create a booking (HTTP {r.status_code})"
    return r.json()
