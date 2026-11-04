"""Exit codes shared by Vouch's commands (`assess`, `vouch-db-setup`)."""

EXIT_BAD_INPUT = 2
"""A wrong argument or setting: running it again won't help until it's fixed."""
EXIT_UNAVAILABLE = 3
"""Something it needs (the database) can't be reached just now: try again shortly."""
