"""Which model the API runs. Sign-in and the real model join here in M3."""

import os
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel

from vouch.fake_model import ScriptedChatModel

FAKE_SCRIPT_ENV = "ASSESS_FAKE_SCRIPT"
DEMO_SCRIPT = Path(__file__).parent / "demo" / "demo.json"


def build_api_model() -> BaseChatModel:
    """The scripted fake model: ASSESS_FAKE_SCRIPT's script, or the bundled demo script.

    Until sign-in exists (M3) the API never builds the real model, so an open URL costs
    nothing. A bad script raises ScriptError.
    """
    return ScriptedChatModel.from_file(os.environ.get(FAKE_SCRIPT_ENV) or DEMO_SCRIPT)
