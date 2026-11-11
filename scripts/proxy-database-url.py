"""The deploy wizard's database URL, pointed at the Cloud SQL Auth Proxy's Unix socket (#594).

    gcloud secrets versions access N --secret=VOUCH_DATABASE_URL ... \\
      | uv run --frozen python -I scripts/proxy-database-url.py SOCKET_DIR -- COMMAND...

It reads VOUCH_DATABASE_URL's value on standard input, as the app reads it (surrounding whitespace
stripped, as vouch.settings does; then psycopg's conninfo_to_dict, as vouch.database does), so
every form the app accepts works: postgresql:// or postgres:// URLs, and key=value settings. It
sets the host to SOCKET_DIR and drops any other address or port, and every TLS setting: the
proxy's local socket is plain, and the proxy encrypts the way to Cloud SQL. Then it runs COMMAND
with the result in VOUCH_DATABASE_URL, in its environment only. The value is never printed, an
argument or a file, and never a shell variable that `bash -x` could trace. A value libpq can't
read is refused (exit 2) naming the setting, never the value: libpq's own message can echo it.
"""

import os
import sys
from collections.abc import Mapping, Sequence

import psycopg
from psycopg import conninfo

SETTING = "VOUCH_DATABASE_URL"
DROPPED = ("hostaddr", "port")  # with host set to the socket's folder, these would point elsewhere


def for_proxy(stored: str, socket_dir: str) -> str:
    """`stored` as libpq settings, its host `socket_dir`, no other address, port or TLS setting.
    Raises ValueError, without the value, when libpq can't read it or it is blank."""
    value = stored.strip()
    try:
        params = conninfo.conninfo_to_dict(value) if value else None
    except psycopg.Error:  # its message may hold the value
        params = None
    if not params:
        raise ValueError(f"{SETTING} isn't a database URL libpq reads (its value isn't shown)")
    kept = {k: v for k, v in params.items() if k not in DROPPED and not k.startswith("ssl")}
    return conninfo.make_conninfo("", **(kept | {"host": socket_dir}))


def run(command: Sequence[str], env: Mapping[str, str]) -> None:
    """Becomes `command`, with `env` (the wizard's own, plus the URL)."""
    os.execvpe(command[0], list(command), dict(env))


def main(argv: Sequence[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print(
            "usage: proxy-database-url.py SOCKET_DIR -- COMMAND...  (the URL on stdin)",
            file=sys.stderr,
        )
        return 2
    try:
        url = for_proxy(sys.stdin.read(), argv[0])
    except ValueError as refused:
        print(f"proxy-database-url: {refused}", file=sys.stderr)
        return 2
    run(argv[2:], {**os.environ, SETTING: url})
    return 0  # not reached: run replaces this process


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
