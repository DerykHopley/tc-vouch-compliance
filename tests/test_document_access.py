"""Who may change a company's evidence (#624, owner decision A): only the company's creator and
developers add a document, a new version or withdraw one; each change is recorded in the trail
of every lead with work under the company."""

from collections.abc import Iterator
from typing import Annotated, Any

import pytest
import test_memory
from company_setup import MUSTERFIRMA_ID
from fastapi import Depends
from fastapi.testclient import TestClient
from test_add_document import PATH, body
from test_current_company import made
from test_database import empty_database, needs_postgres  # noqa: F401
from test_memory import app_on, restart, user_memory
from test_withdraw_document import withdraw_path

from vouch import api
from vouch.added_documents import process_added_documents
from vouch.auth import Role, User

users = test_memory.users
postgres = test_memory.postgres

DOC = "visitor-badge-policy"


@pytest.fixture(autouse=True)
def _fresh_process() -> Iterator[None]:
    restart()
    yield
    restart()


def app_with_roles(url: Any, developers: set[str]) -> Any:
    """An app where only `developers` (user ids) have the developer role; everyone else is a
    compliance lead (local mode makes everyone a developer)."""
    app = app_on(url)

    def role(user: Annotated[User, Depends(api.current_user)]) -> Role:
        return "developer" if user.id in developers else "compliance-lead"

    app.dependency_overrides[api.user_role] = role
    return app


def uid(headers: dict[str, str]) -> str:
    return headers["X-Test-User"]


def on(headers: dict[str, str], profile: str) -> dict[str, str]:
    return headers | {api.PROFILE_HEADER: profile}


def document_entries(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/api/audit-trail", headers=headers)
    assert response.status_code == 200, response.text
    return [e for e in response.json()["entries"] if e["action"].startswith("document-")]


def test_a_lead_who_did_not_create_the_company_is_refused_and_nothing_is_written(users):
    alice, bob = users
    with TestClient(app_with_roles(None, {uid(alice)})) as client:
        company = made(client, alice)
        added = client.post(PATH, json=body(), headers=on(alice, company))
        refused_add = client.post(PATH, json=body("# v2\n"), headers=on(bob, company))
        refused_withdraw = client.post(withdraw_path(DOC), headers=on(bob, company))
        tab = client.get(PATH, headers=on(bob, company)).json()

    assert added.status_code == 201, added.text
    for refused in (refused_add, refused_withdraw):
        assert refused.status_code == 403
        assert refused.json() == {"detail": api.DOCUMENTS_CHANGE_REFUSED}
    kept = process_added_documents().listed(company)
    assert [(d.version, d.withdrawn_at) for d in kept] == [(1, None)]
    assert tab["can_change"] is False


def test_the_creator_of_a_company_may_change_its_documents_without_the_developer_role(users):
    alice, _ = users
    # A company set up by a user who is a lead now (their role changed since): still theirs.
    with TestClient(app_with_roles(None, {uid(alice)})) as client:
        company = made(client, alice)
    with TestClient(app_with_roles(None, set())) as client:
        added = client.post(PATH, json=body(), headers=on(alice, company))
        version = client.post(PATH, json=body("# v2\n"), headers=on(alice, company))
        withdrawn = client.post(withdraw_path(DOC), headers=on(alice, company))
        tab = client.get(PATH, headers=on(alice, company)).json()

    assert (added.status_code, version.status_code, withdrawn.status_code) == (201, 201, 200)
    assert tab["can_change"] is True


def test_on_a_file_defined_company_only_a_developer_may_change_documents(users):
    alice, bob = users
    with TestClient(app_with_roles(None, {uid(alice)})) as client:
        lead = client.post(PATH, json=body(), headers=bob)
        developer = client.post(PATH, json=body(), headers=alice)
        tabs = [client.get(PATH, headers=h).json()["can_change"] for h in (alice, bob)]

    assert lead.status_code == 403
    assert developer.status_code == 201, developer.text
    assert tabs == [True, False]


def test_a_change_is_recorded_in_the_trail_of_every_lead_with_work_under_the_company(users):
    alice, bob = users
    carol = {"X-Test-User": "user_carol_" + uid(alice)[-8:]}
    with user_memory(None, bob, "nordlicht") as memory:
        memory.remember_assessment("thread-1", "1.1.1")
    with user_memory(None, carol, MUSTERFIRMA_ID) as memory:  # work under another company
        memory.remember_assessment("thread-2", "1.1.1")
    with TestClient(app_with_roles(None, {uid(alice)})) as client:
        client.post(PATH, json=body(), headers=alice)
        client.post(withdraw_path(DOC), headers=alice)
        mine, bobs, carols = (document_entries(client, h) for h in (alice, bob, carol))

    assert [e["action"] for e in mine] == ["document-withdraw", "document-add"]
    assert [e["action"] for e in bobs] == ["document-withdraw", "document-add"]
    assert {e["reviewer_name"] for e in bobs} == {e["reviewer_name"] for e in mine}
    assert {e["profile_id"] for e in bobs} == {"nordlicht"}
    assert carols == []


def test_a_lead_whose_only_work_is_a_trail_entry_is_told_too(users):
    alice, bob = users
    with TestClient(app_with_roles(None, {uid(alice)})) as client:
        goal = client.put("/api/goal", json={"goal": "internal-improvement"}, headers=bob)
        client.post(PATH, json=body(), headers=alice)
        bobs = document_entries(client, bob)

    assert goal.status_code == 200, goal.text
    assert [e["action"] for e in bobs] == ["document-add"]


@needs_postgres
def test_on_postgres_every_affected_lead_gets_the_entry_and_a_lead_is_refused(postgres, users):
    alice, bob = users
    with user_memory(postgres.url, bob, "nordlicht") as memory:
        memory.remember_assessment("thread-1", "1.1.1")
    with TestClient(app_with_roles(postgres.url, {uid(alice)})) as client:
        refused = client.post(PATH, json=body(), headers=bob)
        added = client.post(PATH, json=body(), headers=alice)
        withdrawn = client.post(withdraw_path(DOC), headers=alice)
        bobs = document_entries(client, bob)
        mine = document_entries(client, alice)

    assert refused.status_code == 403
    assert (added.status_code, withdrawn.status_code) == (201, 200)
    assert [e["action"] for e in bobs] == ["document-withdraw", "document-add"]
    assert [e["action"] for e in mine] == ["document-withdraw", "document-add"]
