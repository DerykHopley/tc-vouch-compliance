"""Evidence changes in the round comparison (#575, feature #345 Q14): the added documents
added, versioned or withdrawn after a round closed, newest change first. Built-in documents
are never listed."""

import datetime
from collections.abc import Iterator

import pytest
import test_memory
from fastapi.testclient import TestClient
from test_add_document import PATH, body
from test_memory import app_on, restart
from test_rounds import close, start

from vouch.added_documents import AddedDocument
from vouch.rounds import evidence_since, round_comparison

users = test_memory.users

CLOSED = datetime.datetime(2026, 11, 1, 12, tzinfo=datetime.UTC)
DAY = datetime.timedelta(days=1)


def version(
    doc_id: str,
    n: int,
    at: datetime.datetime,
    withdrawn_at: datetime.datetime | None = None,
    title: str | None = None,
) -> AddedDocument:
    return AddedDocument(
        profile_id="nordlicht",
        doc_id=doc_id,
        version=n,
        title=title or doc_id.replace("-", " ").capitalize(),
        kind="policy",
        approval_date=datetime.date(2026, 10, 1),
        review_cycle="yearly",
        text="# Text\n",
        added_by="user_lead",
        added_by_name="Dana Lead",
        added_at=at,
        withdrawn_at=withdrawn_at,
    )


def test_added_new_version_and_withdrawn_after_the_close_are_listed_newest_first():
    kept = [
        version("added-doc", 1, CLOSED + DAY),
        version("versioned", 1, CLOSED - DAY),
        version("versioned", 2, CLOSED - DAY / 2),
        version("versioned", 3, CLOSED + 2 * DAY),
        version("versioned", 4, CLOSED + 3 * DAY, title="Versioned, renamed"),
        version("gone", 1, CLOSED - DAY, withdrawn_at=CLOSED + 4 * DAY),
    ]

    rows = evidence_since(kept, CLOSED)

    assert [(r["doc_id"], r["change"], r["from_version"], r["to_version"]) for r in rows] == [
        ("gone", "withdrawn", None, 1),
        ("versioned", "new-version", 2, 4),
        ("added-doc", "added", None, 1),
    ]
    assert rows[1]["title"] == "Versioned, renamed"  # its newest version's
    assert rows[1]["kind"] == "policy"
    assert rows[0]["at"] == CLOSED + 4 * DAY


def test_added_and_withdrawn_since_is_withdrawn_and_added_with_versions_since_is_added():
    kept = [
        version("both", 1, CLOSED + DAY, withdrawn_at=CLOSED + 2 * DAY),
        version("fresh", 1, CLOSED + DAY),
        version("fresh", 2, CLOSED + 3 * DAY),
    ]

    rows = evidence_since(kept, CLOSED)

    assert [(r["doc_id"], r["change"], r["to_version"]) for r in rows] == [
        ("fresh", "added", 2),
        ("both", "withdrawn", 1),
    ]


def test_changes_at_or_before_the_close_are_not_listed():
    kept = [
        version("old", 1, CLOSED - DAY),
        version("at-close", 1, CLOSED),
        version("withdrawn-before", 1, CLOSED - 2 * DAY, withdrawn_at=CLOSED - DAY),
    ]

    assert evidence_since(kept, CLOSED) == []


def test_round_comparison_carries_the_evidence_changes_and_none_without_added_documents():
    kept = [version("fresh", 1, CLOSED + DAY)]

    with_added = round_comparison([], [], [], {}, added=kept, closed_at=CLOSED)
    without = round_comparison([], [], [], {}, added=[], closed_at=CLOSED)

    assert [r["doc_id"] for r in with_added["evidence"]] == ["fresh"]
    assert without["evidence"] == []


# --- The route ---------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_process() -> Iterator[None]:
    restart()
    yield
    restart()


def test_the_comparison_serves_documents_changed_since_the_round_closed_and_no_built_in_one(
    users,
):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        assert client.post(PATH, json=body(), headers=alice).status_code == 201
        close(client, alice)
        start(client, alice, "Q4 2026")
        nothing = client.get("/api/rounds/1/comparison", headers=alice).json()["evidence"]
        assert client.post(PATH, json=body("# Badges, v2\n"), headers=alice).status_code == 201
        response = client.get("/api/rounds/1/comparison", headers=alice)

    assert nothing == []  # the built-in documents never; the added one before the close
    assert response.status_code == 200, response.text
    (row,) = response.json()["evidence"]
    assert {k: row[k] for k in ("doc_id", "title", "kind", "change")} == {
        "doc_id": "visitor-badge-policy",
        "title": "Visitor badge policy",
        "kind": "policy",
        "change": "new-version",
    }
    assert (row["from_version"], row["to_version"]) == (1, 2)
    assert datetime.datetime.fromisoformat(row["at"]) > datetime.datetime.fromisoformat(
        response.json()["round"]["closed_at"]
    )
