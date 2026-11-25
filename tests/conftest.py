import socket

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """A mistaken live API call must fail the test, even with a local .env."""
    def network_forbidden(*args, **kwargs):
        pytest.fail("Offline tests attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", network_forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", network_forbidden)
