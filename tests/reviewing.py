"""The three-step review through the API (#372), for tests that need a reviewed assessment:
Confirm findings, then each remediation item decided (Plan fixes), then Accept or Reject. And an
assessment accepted before the three-step review, as a checkpoint kept before #372 holds it."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from vouch import graph
from vouch.graph import build_graph, thread_config
from vouch.memory import Memory, process_checkpointer
from vouch.profile import DEFAULT_PROFILE

OWNER = "IT security officer"
"""The owner role an approval names, so a "not drafted" item (no owner) can be approved too."""

ItemDecision = Literal["approve", "discard"]


def confirm(
    client: TestClient,
    thread_id: str,
    overrides: Sequence[dict[str, Any]] = (),
    headers: dict[str, str] | None = None,
) -> Any:
    """Confirm findings, with any overrides: the response."""
    return client.post(
        f"/api/assessments/{thread_id}/findings",
        json={"overrides": list(overrides)},
        headers=headers or {},
    )


def decide(
    client: TestClient,
    thread_id: str,
    requirement_id: str,
    body: Mapping[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One remediation item decided: the item as kept."""
    response = client.post(
        f"/api/assessments/{thread_id}/remediation/{requirement_id}",
        json=dict(body),
        headers=headers or {},
    )
    assert response.status_code == 200, response.text
    return response.json()["item"]


def plan_fixes(
    client: TestClient,
    thread_id: str,
    items: ItemDecision = "approve",
    headers: dict[str, str] | None = None,
) -> list[str]:
    """Decide every remediation item still a draft, each the same way: the requirement ids."""
    shown = client.get(f"/api/assessments/{thread_id}", headers=headers or {})
    assert shown.status_code == 200, shown.text
    drafts = [i for i in shown.json()["assessment"]["remediation"] if i["status"] == "draft"]
    for item in drafts:
        body = {"decision": items} | ({"owner_role": OWNER} if items == "approve" else {})
        decide(client, thread_id, item["requirement_id"], body, headers)
    return [i["requirement_id"] for i in drafts]


def reviewed(
    client: TestClient,
    thread_id: str,
    decision: Literal["accept", "reject"] = "accept",
    *,
    note: str = "",
    overrides: Sequence[dict[str, Any]] = (),
    decisions: Mapping[str, Mapping[str, Any]] | None = None,
    items: ItemDecision = "approve",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The assessment reviewed: its findings confirmed with `overrides` (skipped for a Reject
    with none, which ends the review at its first step), the items in `decisions` decided
    (requirement id to body), and for an Accept every other draft decided as `items`; then the
    decision, with `note`. The reviewed assessment."""
    if decision == "accept" or overrides or decisions:
        confirmed = confirm(client, thread_id, overrides, headers)
        assert confirmed.status_code == 200, confirmed.text
    for requirement_id, body in (decisions or {}).items():
        decide(client, thread_id, requirement_id, body, headers)
    if decision == "accept":
        plan_fixes(client, thread_id, items, headers)
    response = client.post(
        f"/api/assessments/{thread_id}/review",
        json={"decision": decision, "note": note},
        headers=headers or {},
    )
    assert response.status_code == 200, response.text
    return response.json()["assessment"]


def accepted(client: TestClient, thread_id: str, **kwargs: Any) -> dict[str, Any]:
    """The assessment through all three steps and accepted (`reviewed`'s options)."""
    return reviewed(client, thread_id, "accept", **kwargs)


@dataclass(frozen=True)
class ReviewBeforeThreeSteps:
    """graph.Review as it was kept before the three-step review (#372): no `three_step`.
    Named as graph.Review, so a checkpoint holds it as an old one did."""

    decision: str
    note: str
    overrides: list
    reviewer_id: str = ""
    reviewer_name: str = ""
    reviewed_at: str = ""


ReviewBeforeThreeSteps.__module__ = graph.Review.__module__
ReviewBeforeThreeSteps.__name__ = ReviewBeforeThreeSteps.__qualname__ = "Review"


def thread_graph(user: dict[str, str], thread_id: str) -> tuple[Any, dict[str, Any]]:
    """The assessment graph on the process's checkpointer (an app with no database URL), and
    the config of the user's thread under the default profile."""
    scope = Memory(InMemoryStore(), user["X-Test-User"], DEFAULT_PROFILE).scope
    return build_graph(None, checkpointer=process_checkpointer()), thread_config(thread_id, scope)


def accept_as_before_three_steps(
    user: dict[str, str], thread_id: str, note: str = "Accepted before."
) -> None:
    """The waiting assessment accepted before the three-step review: its checkpoint written by
    the review node as one kept before #372 was, with no `three_step`."""
    built, config = thread_graph(user, thread_id)
    review = ReviewBeforeThreeSteps("accept", note, [])
    built.update_state(config, {"review": review}, as_node="review")
