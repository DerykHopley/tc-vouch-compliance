"""The demo round seed (#579, the owner's option C): a compliance lead's work under Nordlicht,
played through the app's own HTTP API on the fake model, so Company › Rounds opens on a closed
round, "Q2 2026", and a clear improvement since. Nothing is written around the app: every step is
a request a lead could make, with its audit entries and real timestamps, and no new data.

The work, in order:

1. the open round is closed (the first round, empty) and "Q2 2026" started;
2. a profile pass, each assessment accepted as Vouch judged it, its remediation items approved:
   the fake model leaves a few controls not evidenced or partly evidenced;
3. an open question logged on one of the weaker controls; "Q2 2026" closed, "Q3 2026" started;
4. two not-evidenced controls and one partly-evidenced control assessed again, the lead
   overriding each requirement still short to evidenced, and accepted;
5. one approved remediation item marked done (the goal switched to Internal improvement for it,
   as marking done needs, then back) and the question answered.

The comparison with "Q2 2026" then shows three controls better, the item done and the question
answered since.

Two targets, one per run (`main`):

- a preview (`scripts/dev.sh`, in memory): `--api http://127.0.0.1:<api port>`. Only a loopback
  address is taken, so the seed never reaches a deployed server; local mode's one user.
- a database (live, or a throwaway one): VOUCH_DATABASE_URL in the environment and `--user`, the
  Clerk user id (`user_...`) whose rounds it seeds. The app runs in this process, built by
  `seeding_app` on settings of its own: no OpenRouter key, so the fake model, whatever the
  environment holds.

It stops before writing anything when "Q2 2026" is kept since the fresh start (seeded already),
or when the user has assessments since the fresh start (a lead's own work: Start afresh first).
"""

import argparse
import ipaddress
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from vouch.auth import USER_ID
from vouch.limits import RATE_REQUESTS
from vouch.profile import DEFAULT_PROFILE
from vouch.settings import DATABASE_URL_ENV, Settings, User, load_database_url

ROUND_NAME = "Q2 2026"
NEXT_ROUND = "Q3 2026"
OWNER_ROLE = "IT security officer"
FIXED = "Evidence added since Q2 2026: the document now covers this requirement."
DONE_NOTE = "Done since Q2 2026: the change was made and recorded."
QUESTION = "Who signs off the updated document, and by when?"
ASKED_OF = "Managing director"
ANSWER = "The managing director signs it off at the next management review."
PROFILE_HEADER = "X-Vouch-Profile"
"""vouch.api's PROFILE_HEADER: the profile a request is for."""
APP_SETTINGS = ("VOUCH_", "CLERK_", "OPENROUTER_", "LANGFUSE_", "ASSESS_", "DISCORD_")
"""The prefixes of the settings vouch.api's own app reads from the environment on import."""
MAX_WAITS = 10
"""Requests refused for the rate (429) the seed waits out before it gives up."""


class SeedError(RuntimeError):
    """The seed stopped: the message says why and what to do."""


class AlreadySeeded(SeedError):
    """ "Q2 2026" is kept since the fresh start: nothing written."""


class NotFresh(SeedError):
    """The user has assessments since the fresh start: nothing written."""


class Client(Protocol):
    """What the seed asks of a client: httpx's, or FastAPI's TestClient (an httpx one)."""

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclass(frozen=True)
class Seeded:
    """What the seed did: the controls improved since "Q2 2026", and where the done item and
    the answered question are."""

    improved: tuple[str, ...]
    done_on: str
    answered_on: str


def _api() -> ModuleType:
    """vouch.api, imported with none of the app's settings in the environment, in local mode:
    its module-level app (which the seed never uses) then can't refuse to start (Clerk keys
    without a mode, as the owner's .envrc has) or reach a database, a key or Langfuse."""
    if "vouch.api" not in sys.modules:
        kept = dict(os.environ)
        try:
            for name in [k for k in os.environ if k.startswith(APP_SETTINGS)]:
                del os.environ[name]
            os.environ["VOUCH_AUTH_MODE"] = "local"
            import vouch.api  # noqa: F401
        finally:
            os.environ.clear()
            os.environ.update(kept)
    return sys.modules["vouch.api"]


def seeding_app(database_url: SecretStr | str | None, user_id: str, name: str) -> FastAPI:
    """The app in this process for one user: local auth mode as that user, named `name` on
    their audit entries; on `database_url`'s database (None: memory); always the bundled fake
    model (no key, no model, no script, no tracing) whatever the environment holds; a request
    rate the seed can't reach."""
    url = SecretStr(database_url) if isinstance(database_url, str) else database_url
    settings = Settings(database_url=url, local_user_name=name, rate_requests=RATE_REQUESTS * 100)
    api = _api()
    app: FastAPI = api.create_app(settings)
    app.dependency_overrides[api.current_user] = lambda: User(id=user_id)
    return app


def _call(client: Client, method: str, path: str, body: Any = None) -> httpx.Response:
    """One request under Nordlicht, waiting out the rate limit; a refusal is a SeedError."""
    headers = {PROFILE_HEADER: DEFAULT_PROFILE}
    for _ in range(MAX_WAITS + 1):
        response = client.request(method, path, json=body, headers=headers)
        if response.status_code != 429:
            break
        time.sleep(float(response.headers.get("Retry-After", "1")))
    if response.status_code >= 400:
        raise SeedError(f"{method} {path} answered {response.status_code}: {response.text[:300]}")
    return response


def _json(client: Client, method: str, path: str, body: Any = None) -> Any:
    return _call(client, method, path, body).json()


def _assessment(client: Client, thread_id: str) -> dict[str, Any]:
    data: dict[str, Any] = _json(client, "GET", f"/api/assessments/{thread_id}")["assessment"]
    return data


def _accept(
    client: Client,
    thread_id: str,
    overrides: Sequence[Mapping[str, str]] = (),
    items: str = "approve",
) -> None:
    """The three-step review: findings confirmed with `overrides`, every draft item decided as
    `items`, then Accept."""
    _call(client, "POST", f"/api/assessments/{thread_id}/findings", {"overrides": list(overrides)})
    for item in _assessment(client, thread_id)["remediation"]:
        if item["status"] == "draft":
            body = {"decision": items} | ({"owner_role": OWNER_ROLE} if items == "approve" else {})
            _call(
                client,
                "POST",
                f"/api/assessments/{thread_id}/remediation/{item['requirement_id']}",
                body,
            )
    _call(
        client, "POST", f"/api/assessments/{thread_id}/review", {"decision": "accept", "note": ""}
    )


def _assess(client: Client, control_id: str) -> str:
    """The control assessed again on the fake model, up to the review: its thread id."""
    stream = _call(client, "POST", "/api/assessments", {"control_id": control_id}).text
    for block in stream.split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in block.splitlines()
            if ": " in line and not line.startswith(":")
        )
        if fields.get("event") == "result":
            return str(json.loads(fields["data"])["assessment"]["thread_id"])
    raise SeedError(f"assessing {control_id} gave no result: {stream[-300:]}")


def _check_fresh(client: Client) -> None:
    rounds = _json(client, "GET", "/api/rounds")["rounds"]
    if any(r["name"] == ROUND_NAME and not r["before_fresh_start"] for r in rounds):
        raise AlreadySeeded(f'"{ROUND_NAME}" is there already: nothing written.')
    if _json(client, "GET", "/api/assessments")["assessments"]:
        raise NotFresh(
            "This user has assessments under Nordlicht since the fresh start, so the seed would "
            "mix with their own work: nothing written. Start afresh first, then seed."
        )


def seed(client: Client, say: Any = None) -> Seeded:
    """Play the lead's work (the module's steps) through `client`. Raises AlreadySeeded or
    NotFresh before writing anything, and SeedError for a request the app refuses."""
    say = say or (lambda _: None)
    _check_fresh(client)
    if _json(client, "GET", "/api/overview")["rounds"]["current"] is not None:
        _call(client, "POST", "/api/rounds/close")
    _call(client, "POST", "/api/rounds", {"name": ROUND_NAME})
    say(f'Started "{ROUND_NAME}"; running a profile pass on the fake model.')

    passed = _json(client, "POST", "/api/profile-pass")["assessments"]
    for a in passed:
        _accept(client, a["thread_id"])
    say(f"Accepted {len(passed)} controls as judged.")
    gaps = [a for a in passed if a["verdict"] == "not-evidenced"][:2]
    partly = [a for a in passed if a["verdict"] == "partly-evidenced"]
    if len(gaps) < 2 or len(partly) < 2:
        raise SeedError("The fake model's pass left too few weaker controls to improve.")
    improved = [*gaps, partly[0]]
    kept_weak = partly[1]  # stays partly evidenced: its item is done, its question answered

    due = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
    question = _json(
        client,
        "POST",
        f"/api/controls/{kept_weak['control_id']}/questions",
        {"question": QUESTION, "asked_of": ASKED_OF, "due_date": due},
    )["question"]
    _call(client, "POST", "/api/rounds/close")
    _call(client, "POST", "/api/rounds", {"name": NEXT_ROUND})
    say(f'Closed "{ROUND_NAME}", started "{NEXT_ROUND}".')

    for a in improved:
        thread_id = _assess(client, a["control_id"])
        short = [
            r["id"]
            for r in _assessment(client, thread_id)["requirements"]
            if r["verdict"] in ("not-evidenced", "partly-evidenced")
        ]
        overrides = [{"requirement_id": r, "verdict": "evidenced", "reason": FIXED} for r in short]
        _accept(client, thread_id, overrides, items="discard")
    say("Assessed " + ", ".join(a["control_id"] for a in improved) + " again: evidenced.")

    approved = [
        i
        for i in _assessment(client, kept_weak["thread_id"])["remediation"]
        if i["status"] == "approved"
    ]
    if not approved:
        raise SeedError(f"{kept_weak['control_id']} has no approved remediation item to mark done.")
    goal = _json(client, "GET", "/api/overview").get("goal", "audit-readiness")
    _call(client, "PUT", "/api/goal", {"goal": "internal-improvement"})
    _call(
        client,
        "POST",
        f"/api/assessments/{kept_weak['thread_id']}/remediation/{approved[0]['requirement_id']}/done",
        {"note": DONE_NOTE},
    )
    if goal != "internal-improvement":
        _call(client, "PUT", "/api/goal", {"goal": goal})
    _call(
        client,
        "POST",
        f"/api/controls/{kept_weak['control_id']}/questions/{question['id']}/answer",
        {"answer": ANSWER},
    )
    say(f"Marked an item on {kept_weak['control_id']} done and answered its question.")
    return Seeded(
        tuple(a["control_id"] for a in improved), kept_weak["control_id"], kept_weak["control_id"]
    )


# --- The command -------------------------------------------------------------------------------

USAGE = (
    "vouch-seed-demo-round needs one target: a preview's API with --api http://127.0.0.1:<port>, "
    f"or {DATABASE_URL_ENV} in the environment with --user <Clerk user id>"
)


def _loopback(api: str) -> bool:
    parts = urlsplit(api)
    if parts.scheme != "http" or not parts.hostname:
        return False
    if parts.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        return False


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Seed the demo round on one target (the module's). 0 seeded; 1 stopped (seeded already, a
    user's own work, or a refused request); 2 a target missing or wrong."""
    parser = argparse.ArgumentParser(
        prog="vouch-seed-demo-round",
        description="Seed Nordlicht's demo round, Q2 2026, on the fake model.",
    )
    parser.add_argument("--api", help="a preview's API on this machine: http://127.0.0.1:<port>")
    parser.add_argument("--user", help="with VOUCH_DATABASE_URL: the Clerk user id (user_...)")
    parser.add_argument("--name", default="Demo lead", help="the name on the audit entries")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    url = load_database_url(env)

    def refuse(why: str) -> int:
        print(f"vouch-seed-demo-round: {why}.", file=sys.stderr)
        return 2

    if args.api and url is not None:
        return refuse(f"give one target, not both: --api or {DATABASE_URL_ENV}")
    if args.api:
        if not _loopback(args.api):
            return refuse(
                f"--api takes a preview on this machine only (http://127.0.0.1:<port>), not {args.api}"
            )
        return _run(lambda: httpx.Client(base_url=args.api.rstrip("/"), timeout=120))
    if url is None:
        return refuse(USAGE)
    if not args.user or not USER_ID.fullmatch(args.user):
        return refuse(
            f"with {DATABASE_URL_ENV}, --user takes the Clerk user id (user_...) to seed for"
        )
    from fastapi.testclient import TestClient

    return _run(lambda: TestClient(seeding_app(url, args.user, args.name)))


def _run(make: Any) -> int:
    def say(line: str) -> None:
        print(line, file=sys.stderr)

    try:
        with make() as client:
            seed(client, say)
    except SeedError as error:
        say(f"vouch-seed-demo-round: {error}")
        return 1
    say(f'Seeded: Company › Rounds now compares with "{ROUND_NAME}".')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
