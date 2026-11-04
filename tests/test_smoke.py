import socket

import pytest
from pytest_socket import SocketBlockedError


def test_package_imports():
    import vouch

    assert vouch.__name__ == "vouch"


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_suite_runs_with_sockets_disabled():
    with pytest.raises(SocketBlockedError):
        socket.socket()
