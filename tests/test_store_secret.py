"""scripts/store-secret.sh (#130): a secret piped in is stored whole in the keyring, multi-line
too, and only its size is said, never its value.

`secret-tool store` typed at its prompt keeps only the first line, so a PEM key (Clerk's
CLERK_JWT_KEY) was once stored as its 26-character header and Vouch didn't start. With a fake
secret-tool that keeps each entry in a folder, exactly as it was given on standard input."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "store-secret.sh"
BASH = shutil.which("bash") or "/bin/bash"

FAKE_SECRET_TOOL = r"""#!/bin/sh
echo "secret-tool $*" >> "$CALLS"
case "$1" in
  store) last=""; for a in "$@"; do last=$a; done; cat > "$KEYRING/$last" ;;
  lookup) [ -f "$KEYRING/$3" ] && cat "$KEYRING/$3" ;;
  *) exit 2 ;;
esac
"""

PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAplantedLineOne\n"
    "plantedLineTwo+/==\n"
    "-----END PUBLIC KEY-----\n"
)


@pytest.fixture
def keyring(tmp_path):
    bin_dir, store = tmp_path / "bin", tmp_path / "keyring"
    bin_dir.mkdir()
    store.mkdir()
    (bin_dir / "secret-tool").write_text(FAKE_SECRET_TOOL)
    (bin_dir / "secret-tool").chmod(0o755)
    calls = tmp_path / "calls.log"

    def run(value: str, *args: str):
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "KEYRING": str(store),
            "CALLS": str(calls),
        }
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            input=value,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    run.store = store
    return run


def test_a_multi_line_pem_piped_in_is_stored_whole_and_only_its_size_said(keyring):
    done = keyring(PEM, "clerk-vouch-jwt-key")

    assert done.returncode == 0, done.stderr
    assert (keyring.store / "clerk-vouch-jwt-key").read_text() == PEM
    assert done.stdout.strip() == f"stored clerk-vouch-jwt-key: {len(PEM)} characters, 4 lines"
    for line in PEM.splitlines():
        assert line not in done.stdout + done.stderr  # the value is never shown


def test_a_one_line_value_is_stored_too(keyring):
    done = keyring("sk_test_plantedClerkSecret123\n", "clerk-vouch-secret")

    assert done.returncode == 0, done.stderr
    assert (keyring.store / "clerk-vouch-secret").read_text() == "sk_test_plantedClerkSecret123\n"
    assert "plantedClerkSecret" not in done.stdout + done.stderr


@pytest.mark.parametrize("value", ["", "\n", "   \n\t\n"])
def test_an_empty_value_is_refused(keyring, value):
    done = keyring(value, "clerk-vouch-secret")

    assert done.returncode == 1
    assert "empty" in done.stderr
    assert not (keyring.store / "clerk-vouch-secret").exists()


def test_a_pem_missing_its_end_line_is_refused_without_showing_it(keyring):
    cut = "".join(PEM.splitlines(keepends=True)[:2])  # what a prompt kept, and a line more

    done = keyring(cut, "clerk-vouch-jwt-key")

    assert done.returncode == 1
    assert "-----END" in done.stderr
    assert "plantedLineOne" not in done.stdout + done.stderr
    assert not (keyring.store / "clerk-vouch-jwt-key").exists()


def test_without_a_service_name_it_says_how_to_use_it(keyring):
    done = keyring(PEM)

    assert done.returncode == 2
    assert "usage" in done.stderr


def test_deploy_md_and_the_wizard_name_the_script():
    for doc in (ROOT / "DEPLOY.md", ROOT / "scripts" / "deploy-wizard.sh"):
        text = doc.read_text()
        assert "scripts/store-secret.sh" in text, doc
        assert "secret-tool store --label" not in text, doc  # its prompt keeps one line only
