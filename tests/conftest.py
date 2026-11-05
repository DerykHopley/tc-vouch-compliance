"""Tests start from no Vouch settings, whatever the developer's shell exports.

`vouch.api` builds its app from the environment when imported, so this runs first.
"""

import os

from vouch.settings import ENV_NAMES

for name in ENV_NAMES:
    os.environ.pop(name, None)
