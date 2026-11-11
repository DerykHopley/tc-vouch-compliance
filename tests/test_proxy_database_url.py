"""scripts/proxy-database-url.py (#594): the deploy wizard's database URL, pointed at the Cloud SQL
Auth Proxy's Unix socket, read as the app reads it (psycopg's conninfo_to_dict, stripped as
vouch.settings reads it). Every value here holds "planted", so a leak is easy to find."""

import importlib.util
import sys
from pathlib import Path

import pytest
from psycopg import conninfo

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "proxy-database-url.py"
SOCKETS = "/cloudsql-proxy/vouch-demo-123:europe-west1:vouch-db"


def helper():
    spec = importlib.util.spec_from_file_location("proxy_database_url", HELPER)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize(
    ("stored", "kept"),
    [
        (
            "postgresql://vouch:plantedPw@/vouch?host=/cloudsql/p:eu:db",
            {"user": "vouch", "password": "plantedPw", "dbname": "vouch"},
        ),
        (
            "postgresql://vouch:pl%40nted:p?w#d&x=y@/vouch?host=/cloudsql/p:eu:db",
            {"user": "vouch", "password": "pl@nted:p?w#d&x=y", "dbname": "vouch"},
        ),
        (
            "postgresql://vouch:planted%2F%3A%40@/vouch?host=%2Fcloudsql%2Fp%3Aeu%3Adb",
            {"user": "vouch", "password": "planted/:@", "dbname": "vouch"},
        ),
        (
            "postgresql://vouch:planted@10.1.2.3:5432/vouch?connect_timeout=10",
            {"user": "vouch", "password": "planted", "dbname": "vouch", "connect_timeout": "10"},
        ),
        (
            "postgres://vouch:planted@localhost/vouch?port=5432&hostaddr=10.0.0.1",
            {"user": "vouch", "password": "planted", "dbname": "vouch"},
        ),
        (
            "host=/cloudsql/p:eu:db user=vouch password='plan ted\\'s' dbname=vouch",
            {"user": "vouch", "password": "plan ted's", "dbname": "vouch"},
        ),
        (
            "  \n postgresql://vouch:planted@/vouch?host=/cloudsql/p:eu:db \r\n",
            {"user": "vouch", "password": "planted", "dbname": "vouch"},
        ),
        (
            (
                "postgresql://vouch:planted@db.example:5432/vouch?sslmode=require"
                "&sslrootcert=/ca.pem&sslcert=/c.pem&sslkey=/k.pem&application_name=vouch"
            ),
            {
                "user": "vouch",
                "password": "planted",
                "dbname": "vouch",
                "application_name": "vouch",
            },
        ),
    ],
    ids=[
        "unix-socket",
        "special-password",
        "encoded",
        "tcp",
        "postgres-scheme",
        "key-value",
        "whitespace",
        "sslmode-require",
    ],
)
def test_the_url_points_at_the_proxy_s_socket_with_everything_else_kept(stored, kept):
    pointed = helper().for_proxy(stored, SOCKETS)
    # The proxy's local socket is plain (the proxy encrypts), so no TLS setting survives; no other
    # host, address or port either.
    assert conninfo.conninfo_to_dict(pointed) == kept | {"host": SOCKETS}


@pytest.mark.parametrize(
    "stored",
    ["", "  \n", "postgresql://vouch:planted@/vouch?nonsense=planted", "planted"],
    ids=["empty", "blank", "unknown-parameter", "not-a-url"],
)
def test_a_url_libpq_can_t_read_is_refused_without_its_value(stored):
    with pytest.raises(ValueError) as refused:
        helper().for_proxy(stored, SOCKETS)
    assert "planted" not in str(refused.value)
    # libpq's own error, which may hold the value, isn't chained, so no traceback shows it.
    assert refused.value.__cause__ is None and refused.value.__context__ is None


def test_main_refuses_a_bad_url_naming_the_setting_not_the_value(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _Stdin("postgresql://vouch:planted@/x?nonsense=planted"))
    module = helper()
    ran = []
    monkeypatch.setattr(module, "run", lambda command, env: ran.append(command))
    assert module.main([SOCKETS, "--", "true"]) == 2
    out = capsys.readouterr()
    assert "VOUCH_DATABASE_URL" in out.err
    assert "planted" not in out.out + out.err
    assert ran == []


def test_main_gives_the_command_the_url_in_its_environment_only(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stdin("postgresql://vouch:planted@/vouch?host=/cloudsql/x"))
    module = helper()
    ran = []
    monkeypatch.setattr(module, "run", lambda command, env: ran.append((command, env)))
    module.main([SOCKETS, "--", "docker", "run", "-e", "VOUCH_DATABASE_URL", "image"])
    [(command, env)] = ran
    assert command == ["docker", "run", "-e", "VOUCH_DATABASE_URL", "image"]
    assert conninfo.conninfo_to_dict(env["VOUCH_DATABASE_URL"])["host"] == SOCKETS
    assert "planted" not in " ".join(command)


def test_main_without_a_command_is_a_usage_error(capsys):
    assert helper().main([SOCKETS]) == 2
    assert "usage" in capsys.readouterr().err


class _Stdin:
    def __init__(self, text: str) -> None:
        self.text = text

    def read(self) -> str:
        return self.text
