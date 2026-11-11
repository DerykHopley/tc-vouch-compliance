"""Withdrawing an added document (#586, feature #345, Q11): `POST /api/documents/{id}/withdraw`
marks it withdrawn with one `document-withdraw` entry, so it is no longer searched; it stays
listed as Withdrawn. And the "New evidence: assess again" reason (Q10): a control whose newest
assessment cites an earlier version of an added document."""

import datetime
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from test_add_document import PATH, added, body
from test_database import empty_database, needs_postgres  # noqa: F401
from test_memory import app_on, restart
from test_plan import assessment

from vouch import api
from vouch.added_documents import added_index, process_added_documents
from vouch.citations import Citation
from vouch.documents_tab import profile_documents
from vouch.graph import RequirementVerdict
from vouch.isa import load_base_framework
from vouch.plan import newer_evidence
from vouch.profile import load_named_profile

users = test_memory.users
postgres = test_memory.postgres


@pytest.fixture(autouse=True)
def _fresh_process() -> Iterator[None]:
    restart()
    yield
    restart()


def withdraw_path(doc_id: str) -> str:
    return f"{PATH}/{doc_id}/withdraw"


def withdraw_entries(client: TestClient, user: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=user)
    assert response.status_code == 200, response.text
    return [e for e in response.json()["entries"] if e["action"] == "document-withdraw"]


def test_withdraw_marks_it_withdrawn_with_one_entry_and_it_is_no_longer_searched(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        client.post(PATH, json=body(), headers=alice)
        client.post(PATH, json=body("# Badges, v2\n"), headers=alice)
        response = client.post(withdraw_path("visitor-badge-policy"), headers=alice)
        again = client.post(withdraw_path("visitor-badge-policy"), headers=alice)
        entries = withdraw_entries(client, alice)

    assert response.status_code == 200, response.text
    assert response.json()["document"]["id"] == "visitor-badge-policy"
    assert response.json()["document"]["withdrawn"] is True
    assert again.status_code == 200, again.text  # already withdrawn: nothing more written
    assert all(d.withdrawn_at is not None for d in process_added_documents().listed("nordlicht"))
    assert added_index().searchable("nordlicht") == []
    assert [e["evidence"] for e in entries] == [
        [{"doc_id": "visitor-badge-policy", "version": "2"}]
    ]
    assert {e["profile_id"] for e in entries} == {"nordlicht"}


def test_a_built_in_id_is_refused_and_nothing_is_written(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        response = client.post(withdraw_path("authentication-standard"), headers=alice)
        entries = withdraw_entries(client, alice)

    assert response.status_code == 400
    assert response.json() == {"detail": api.BUILT_IN_NOT_WITHDRAWN}
    assert entries == []


def test_an_unknown_id_is_a_404(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        response = client.post(withdraw_path("no-such-document"), headers=alice)
        entries = withdraw_entries(client, alice)

    assert response.status_code == 404
    assert response.json() == {"detail": api.NO_ADDED_DOCUMENT}
    assert entries == []


def test_a_withdrawn_document_is_listed_as_withdrawn(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        client.post(PATH, json=body(), headers=alice)
        client.post(withdraw_path("visitor-badge-policy"), headers=alice)
        shown = client.get(PATH, headers=alice).json()

    row = next(d for d in shown["documents"] if d["id"] == "visitor-badge-policy")
    assert (row["origin"], row["withdrawn"], row["version"]) == ("added", True, "1")
    assert shown["words"]["withdrawn"] == "Withdrawn"


@needs_postgres
def test_on_postgres_the_withdraw_and_its_entry_are_kept_together(postgres, users):
    alice, _ = users
    with TestClient(app_on(postgres.url)) as client:
        client.post(PATH, json=body(), headers=alice)
        response = client.post(withdraw_path("visitor-badge-policy"), headers=alice)
        again = client.post(withdraw_path("visitor-badge-policy"), headers=alice)
        entries = withdraw_entries(client, alice)
        listed = client.get(PATH, headers=alice).json()["documents"]

    assert response.status_code == 200, response.text
    assert again.status_code == 200, again.text
    assert [e["evidence"] for e in entries] == [
        [{"doc_id": "visitor-badge-policy", "version": "1"}]
    ]
    assert next(d for d in listed if d["id"] == "visitor-badge-policy")["withdrawn"] is True


# --- profile_documents ----------------------------------------------------------------------


def test_profile_documents_marks_a_withdrawn_document_and_others_not():
    at = datetime.datetime(2026, 11, 10, tzinfo=datetime.UTC)
    listed = profile_documents(
        load_named_profile("nordlicht"),
        list(load_base_framework().controls),
        {},
        datetime.date(2026, 11, 9),
        [added(withdrawn_at=at), added(doc_id="kept", title="Kept policy")],
    )

    rows = {d.summary.id: d.as_dict() for d in listed if d.added}
    assert rows["visitor-badge-policy"]["withdrawn"] is True
    assert rows["kept"]["withdrawn"] is False
    assert {d.as_dict()["withdrawn"] for d in listed if not d.added} == {False}


# --- the new-evidence reason ----------------------------------------------------------------


def citing(doc_id: str, version: str, *, status: str = "accepted", control_id: str = "4.1.2"):
    cited = Citation(
        doc_id, version, "Badges", "Every visitor wears a badge.", "documented", False, None
    )
    made = assessment("t", {"M1": "evidenced"}, status=status, control_id=control_id)
    requirement = RequirementVerdict("M1", "must", "Text.", "evidenced", "Reason.", (cited,))
    return replace(made, requirements=[requirement])


NEWEST = added(version=2)


def test_new_evidence_when_the_newest_assessment_cites_an_earlier_version():
    assert newer_evidence({"4.1.2": citing("visitor-badge-policy", "1")}, [NEWEST]) == {
        "4.1.2": [NEWEST]
    }


def test_new_evidence_also_while_the_assessment_waits_for_review():
    latest = {"4.1.2": citing("visitor-badge-policy", "1", status="waiting-for-review")}
    assert newer_evidence(latest, [NEWEST]) == {"4.1.2": [NEWEST]}


@pytest.mark.parametrize(
    "latest",
    [
        {"4.1.2": citing("visitor-badge-policy", "2")},  # cites the newest
        {"4.1.2": citing("authentication-standard", "3.1")},  # cites another document
        {"4.1.2": citing("visitor-badge-policy", "1", status="rejected")},  # counts for nothing
        {},  # not assessed
    ],
)
def test_no_new_evidence_otherwise(latest):
    assert newer_evidence(latest, [NEWEST]) == {}


def test_no_new_evidence_for_a_withdrawn_document():
    """Only a newer version raises it; the withdrawn one isn't given (out of scope, #586)."""
    assert newer_evidence({"4.1.2": citing("visitor-badge-policy", "1")}, []) == {}
