"""Which model to run. Sign-in and the API's real model join here in M3."""

import os
from pathlib import Path
from typing import overload

from vouch.fake_model import ScriptedChatModel

FAKE_SCRIPT_ENV = "ASSESS_FAKE_SCRIPT"
DEMO_SCRIPT = Path(__file__).parent / "demo" / "demo.json"


@overload
def fake_model_from_env(default: Path) -> ScriptedChatModel: ...
@overload
def fake_model_from_env(default: None = None) -> ScriptedChatModel | None: ...
def fake_model_from_env(default: Path | None = None) -> ScriptedChatModel | None:
    """The scripted fake model playing ASSESS_FAKE_SCRIPT's script, else `default`'s.

    None when neither names a script. A bad script raises ScriptError.
    """
    script = os.environ.get(FAKE_SCRIPT_ENV) or default
    return ScriptedChatModel.from_file(script) if script else None
