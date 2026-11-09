"""Companies made in the app (#385): kept in the database (or this process), listed and opened
beside the file profiles, their evidence chosen documents of the sample library; created only
by a developer."""

import json
import logging
import threading
from collections.abc import Iterator
from typing import Any

import pytest
import test_memory
from fastapi.testclient import TestClient
from profiles_dir import BROKEN, make_profiles
from test_auth import FakeClerk, bearer, clerk_app, token
from test_database import needs_postgres
from test_memory import app_on, as_user, assess, restart

from vouch import company_profiles
from vouch.api import PROFILE_HEADER, app_company_profiles, create_app, current_user
from vouch.company_profiles import (
    PostgresCompanyProfiles,
    ProcessCompanyProfiles,
    StoredProfile,
    candidate_ids,
    company_id,
    read_stored_once,
)
from vouch.database import Database, DatabaseUnavailable
from vouch.graph import build_graph, state_serializer, thread_config
from vouch.memory import process_checkpointer
from vouch.paths import SAMPLE_LIBRARY_DIR
from vouch.profile import BrokenProfile, ComplianceProfile, read_profile_once
from vouch.settings import Settings

database_url = test_memory.database_url
postgres = test_memory.postgres
empty_database = test_memory.empty_database
users = test_memory.users

POLICIES = ["authentication-standard", "backup-policy"]
RECORDS = ["sign-in-settings-record"]
MUSTERFIRMA = {
    "company": "Musterfirma Süd GmbH",
    "country": "Germany",
    "answer_language": "en",
    "protection_level": "normal",
    "evidence": {"policies": POLICIES, "records": RECORDS},
}
MUSTERFIRMA_ID = "musterfirma-sud-gmbh"
FIELDS = {
    "company": "Musterfirma Süd GmbH",
    "country": "Germany",
    "answer_language": "de",
    "protection_level": "high",
    "not_applicable": [{"control": "1.1.1", "requirement": None, "reason": "Not ours."}],
    "evidence": {"policies": POLICIES, "records": RECORDS},
}


@pytest.fixture(autouse=True)
def _fresh_process():
    """Each test starts as a new process: no company kept in it, nothing cached."""
    company_profiles.process_company_profiles.cache_clear()
    company_profiles._read_row.cache_clear()
    read_profile_once.cache_clear()
    yield
    company_profiles.process_company_profiles.cache_clear()


def create(client: TestClient, headers: dict[str, str], body: dict[str, Any] = MUSTERFIRMA):
    return client.post("/api/developer/profiles", json=body, headers=headers)


def listed_ids(client: TestClient, headers: dict[str, str]) -> list[str]:
    response = client.get("/api/profiles", headers=headers)
    assert response.status_code == 200, response.text
    return [p["id"] for p in response.json()["profiles"]]


# --- The id a company's name gives ------------------------------------------------------------


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ("Musterfirma Süd GmbH", MUSTERFIRMA_ID),
        ("Nordlicht", "nordlicht"),
        ("  Ärger & Öl  AG ", "arger-ol-ag"),
        ("!!!", "company"),
        ("中文", "company"),
        ("a" * 70, "a" * 63),
        ("a" * 62 + " b", "a" * 62),  # cut, and the cut's trailing - dropped
    ],
)
def test_a_company_name_gives_a_lowercase_ascii_id_fitting_a_profile_name(company, expected):
    assert company_id(company) == expected


def test_a_taken_id_gives_the_first_free_numbered_one_still_fitting_a_profile_name():
    found = candidate_ids("nordlicht", {"nordlicht", "nordlicht-2"})
    assert [next(found), next(found)] == ["nordlicht-3", "nordlicht-4"]
    long = "a" * 63
    assert next(candidate_ids(long, {long})) == "a" * 61 + "-2"


# --- Each backend keeps a company and loads its profile --------------------------------------


@pytest.fixture(params=["memory", pytest.param("postgres", marks=needs_postgres)])
def store(request) -> Iterator[company_profiles.CompanyProfiles]:
    if request.param == "memory":
        yield ProcessCompanyProfiles()
        return
    yield PostgresCompanyProfiles(request.getfixturevalue("postgres"))


def test_a_company_kept_and_read_back_loads_as_its_compliance_profile(store):
    profile_id = store.create(FIELDS["company"], FIELDS, "user_dev", taken=set())

    [row] = store.all()
    assert row == store.one(profile_id)
    assert (row.id, row.created_by) == (MUSTERFIRMA_ID, "user_dev")
    profile = read_stored_once(row)
    assert isinstance(profile, ComplianceProfile)
    assert profile.id == MUSTERFIRMA_ID
    assert (profile.company, profile.country) == ("Musterfirma Süd GmbH", "Germany")
    assert (profile.answer_language, profile.protection_level) == ("de", "high")
    assert [(n.control, n.requirement, n.reason) for n in profile.not_applicable] == [
        ("1.1.1", None, "Not ours.")
    ]
    assert profile.overlays == ()
    policies, records = profile.evidence_sources["policies"], profile.evidence_sources["records"]
    assert policies.folder == (SAMPLE_LIBRARY_DIR / "policies").resolve()
    assert records.folder == (SAMPLE_LIBRARY_DIR / "records").resolve()
    assert (policies.documents, records.documents) == (tuple(POLICIES), tuple(RECORDS))
    assert read_stored_once(store.one(profile_id)) is profile  # validated once per row
    assert store.one("nobody") is None


def test_a_company_name_taken_gives_numbered_ids_oldest_listed_first(store):
    ids = [store.create("Nordlicht", FIELDS, "user_dev", taken={"nordlicht"}) for _ in range(3)]

    assert ids == ["nordlicht-2", "nordlicht-3", "nordlicht-4"]
    assert [row.id for row in store.all()] == ids


def test_two_creates_racing_for_one_id_both_succeed_with_different_ids(store):
    made: list[str] = []
    start = threading.Barrier(4)

    def make() -> None:
        start.wait()
        made.append(store.create("Racing AG", FIELDS, "user_dev", taken=set()))

    threads = [threading.Thread(target=make) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(made) == ["racing-ag", "racing-ag-2", "racing-ag-3", "racing-ag-4"]


def test_a_source_with_no_document_chosen_is_left_out_not_the_whole_folder(store):
    fields = FIELDS | {"evidence": {"policies": POLICIES, "records": []}}
    profile = read_stored_once(store.one(store.create("Acme", fields, "u", taken=set())))

    assert isinstance(profile, ComplianceProfile)
    assert set(profile.evidence_sources) == {"policies"}


# --- Listed and opened beside the file profiles (stories 1 and 3, backend) --------------------


def evidence_of(url, user_id: str, thread_id: str) -> tuple[set[str], dict[str, list[str]]]:
    """The documents the assessment's searches found, and the choices its state keeps."""
    config = thread_config(thread_id, json.dumps([user_id, MUSTERFIRMA_ID]))
    if url is None:
        values = build_graph(None, checkpointer=process_checkpointer()).get_state(config).values
    else:
        with Database(url) as db, db.checkpointer(state_serializer()) as saver:
            values = build_graph(None, checkpointer=saver).get_state(config).values
    return {p["doc_id"] for p in values["evidence"]}, values["document_choices"]


def test_a_developer_creates_a_company_listed_and_opened_beside_the_file_profiles(
    database_url, users
):
    alice, _ = users
    restart()
    with TestClient(app_on(database_url)) as client:
        made = create(client, alice)
        listed = client.get("/api/profiles", headers=alice).json()
        under = alice | {PROFILE_HEADER: MUSTERFIRMA_ID}
        overview = client.get("/api/overview", headers=under)
        thread_id = assess(client, under, "4.1.2")

    assert made.status_code == 201, made.text
    assert made.json() == {
        "id": MUSTERFIRMA_ID,
        "company": "Musterfirma Süd GmbH",
        "country": "Germany",
        "answer_language": "en",
    }
    assert [p["id"] for p in listed["profiles"]] == ["alpenwerk", "nordlicht", MUSTERFIRMA_ID]
    assert listed["profiles"][-1] == made.json()
    assert listed["default"] == "nordlicht"
    assert overview.status_code == 200
    assert overview.json()["company"] == "Musterfirma Süd GmbH"
    found, choices = evidence_of(database_url, alice["X-Test-User"], thread_id)
    assert choices == {"policies": POLICIES, "records": RECORDS}
    assert found and found <= {*POLICIES, *RECORDS}  # only its three documents


def test_creating_names_already_taken_gives_numbered_ids(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        made = [
            create(client, alice, MUSTERFIRMA | {"company": company}).json()["id"]
            for company in ["Musterfirma Süd GmbH", "Musterfirma Süd GmbH", "Nordlicht"]
            + ["Sample library", "!!!"]
        ]

    assert made == [
        MUSTERFIRMA_ID,
        f"{MUSTERFIRMA_ID}-2",
        "nordlicht-2",
        "sample-library-2",
        "company",
    ]


def test_a_broken_profiles_folder_name_is_taken_too(tmp_path, users):
    alice, _ = users
    app = create_app(Settings(profiles_dir=make_profiles(tmp_path)))
    app.dependency_overrides[current_user] = as_user
    with TestClient(app) as client:
        made = create(client, alice, MUSTERFIRMA | {"company": BROKEN})

    assert made.json()["id"] == f"{BROKEN}-2"


OK_NA = {"control": "1.1.1", "reason": "Not ours."}


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"company": "   "}, "company"),
        ({"company": "a" * 101}, "company"),
        ({"country": ""}, "country"),
        ({"company": "Nul\x00 AG"}, "company"),
        ({"answer_language": "fr"}, "answer_language"),
        ({"protection_level": "medium"}, "protection_level"),
        ({"evidence": {"policies": POLICIES, "patch": ["x"]}}, "patch"),
        ({"evidence": {"policies": [], "records": []}}, "evidence"),
        ({"evidence": {"policies": ["no-such-policy"]}}, "no-such-policy"),
        ({"evidence": {"policies": ["../records/training-record"]}}, "evidence"),
        ({"evidence": {"policies": ["backup-policy", "backup-policy.md"]}}, "named twice"),
        ({"not_applicable": [{"control": "99.9.9", "reason": "x"}]}, "not_applicable.0.control"),
        (
            {"not_applicable": [{"control": "1.1.1", "requirement": "M9", "reason": "x"}]},
            "not_applicable.0.requirement",
        ),
        (
            {"not_applicable": [{"control": "1.6.2", "requirement": "H1", "reason": "x"}]},
            "not_applicable.0.requirement",
        ),
        ({"not_applicable": [OK_NA, OK_NA]}, "not_applicable.1"),
        ({"not_applicable": [{"control": "1.1.1", "reason": " "}]}, "reason"),
        ({"not_applicable": [{"control": "1.1.1", "reason": "x\x00"}]}, "not_applicable.0"),
    ],
)
def test_a_refused_company_answers_422_naming_the_field_and_keeps_nothing(change, field, users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        refused = create(client, alice, MUSTERFIRMA | change)
        after = listed_ids(client, alice)

    assert refused.status_code == 422, refused.text
    assert field in refused.text
    assert str(SAMPLE_LIBRARY_DIR) not in refused.text
    assert after == ["alpenwerk", "nordlicht"]


def test_a_requirement_not_brought_in_at_its_protection_level_is_refused_only_there(users):
    """1.6.2 H1 is a high requirement (the 422 case above, at normal): at high it is taken."""
    alice, _ = users
    na = [{"control": "1.6.2", "requirement": "H1", "reason": "x"}]
    with TestClient(app_on(None)) as client:
        made = create(
            client, alice, MUSTERFIRMA | {"protection_level": "high", "not_applicable": na}
        )

    assert made.status_code == 201, made.text


@pytest.fixture
def clerk() -> FakeClerk:
    return FakeClerk({"user_alice": {}, "user_dev": {"role": "developer"}})


def test_a_compliance_leads_create_is_403_and_signed_out_401_keeping_nothing(clerk):
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    lead = create(client, bearer(token("user_alice")))
    signed_out = create(client, {})
    developer = client.get("/api/profiles", headers=bearer(token("user_dev")))

    assert lead.status_code == 403
    assert signed_out.status_code == 401
    assert [p["id"] for p in developer.json()["profiles"]] == ["alpenwerk", "nordlicht"]


def test_a_stored_company_no_longer_valid_is_broken_and_noted_path_free(users):
    alice, _ = users
    stale = FIELDS | {"evidence": {"policies": ["retired-policy"]}}  # gone from the library
    kept = company_profiles.process_company_profiles()
    kept.create("Stale GmbH", stale, "user_dev", taken=set())
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        listed = listed_ids(client, alice)
        notes = client.get("/api/developer/settings", headers=alice).json()["broken_profiles"]
        opened = client.get("/api/overview", headers=alice | {PROFILE_HEADER: "stale-gmbh"})

    assert "stale-gmbh" not in listed
    [note] = notes
    assert note["id"] == "stale-gmbh"
    assert "retired-policy" in note["problem"]
    assert "/" not in note["problem"]
    assert opened.status_code == 500
    assert isinstance(read_stored_once(kept.one("stale-gmbh")), BrokenProfile)


class UnreadableStore:
    """A store that fails the test when read, or raises `error`."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def _read(self) -> Any:
        if self.error is None:
            pytest.fail("the company profiles were read")
        raise self.error

    def create(self, *args: Any) -> str:
        return self._read()

    def all(self) -> list[StoredProfile]:
        return self._read()

    def one(self, profile_id: str) -> StoredProfile | None:
        return self._read()


def app_with_store(store: UnreadableStore) -> TestClient:
    app = app_on(None)
    app.dependency_overrides[app_company_profiles] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


def test_a_request_under_a_file_profile_never_reads_the_company_profiles(users):
    alice, _ = users
    with app_with_store(UnreadableStore()) as client:
        for profile_id in ["nordlicht", "alpenwerk"]:
            under = alice | {PROFILE_HEADER: profile_id}
            assert client.get("/api/overview", headers=under).status_code == 200
        assert client.get("/api/overview", headers=alice).status_code == 200


def test_with_the_database_down_the_file_profiles_are_listed_and_a_stored_id_is_503(users, caplog):
    alice, _ = users
    down = UnreadableStore(DatabaseUnavailable("the database is unavailable: test"))
    with caplog.at_level(logging.WARNING), app_with_store(down) as client:
        listed = listed_ids(client, alice)
        opened = client.get("/api/overview", headers=alice | {PROFILE_HEADER: MUSTERFIRMA_ID})
        made = create(client, alice)

    assert listed == ["alpenwerk", "nordlicht"]
    assert "file profiles only" in caplog.text
    assert opened.status_code == 503
    assert opened.json()["unavailable"] == "database"
    assert made.status_code == 503


def test_an_id_no_profile_has_is_still_404(users):
    alice, _ = users
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        assert (
            client.get("/api/overview", headers=alice | {PROFILE_HEADER: "nobody"}).status_code
            == 404
        )
