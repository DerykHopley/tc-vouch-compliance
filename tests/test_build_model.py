import pytest

from vouch.cli import ConfigError, build_model


def test_no_fake_script_and_no_key_is_a_config_error(monkeypatch):
    monkeypatch.delenv("ASSESS_FAKE_SCRIPT", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        build_model()
