import pytest

from vouch.cli import build_model
from vouch.settings import SettingsError, load_settings


def test_no_fake_script_and_no_key_is_a_settings_error():
    with pytest.raises(SettingsError, match="OPENROUTER_API_KEY"):
        build_model(load_settings({}))
