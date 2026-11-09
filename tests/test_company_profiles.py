"""Companies made in the app (#385): kept in the database (or this process), listed and opened
beside the file profiles, their evidence chosen documents of the sample library; created only
by a developer."""

import json
import logging
import shutil
import threading
from collections.abc import Iterator
from typing import Any

import pytest
import test_memory
from company_setup import MUSTERFIRMA, MUSTERFIRMA_ID, POLICIES, RECORDS
from fastapi.testclient import TestClient
from profiles_dir import BROKEN, make_profiles
from test_auth import FakeClerk, bearer, clerk_app, token
from test_database import needs_postgres
from test_memory import app_on, as_user, assess, restart

from vouch import api, company_profiles
from vouch.api import PROFILE_HEADER, app_company_profiles, create_app, current_user
from vouch.audit import now
from vouch.company_profiles import (
    MAX_COMPANY_LENGTH,
    CompanySetup,
    PostgresCompanyProfiles,
    ProcessCompanyProfiles,
    StoredProfile,
    candidate_ids,
    company_id,
    compliance_profile_of,
    read_stored_once,
)
from vouch.database import AUDIT_TABLE, COMPANY_PROFILES_TABLE, Database, DatabaseUnavailable
from vouch.graph import build_graph, state_serializer, thread_config
from vouch.memory import process_checkpointer
from vouch.paths import SAMPLE_LIBRARY_DIR
from vouch.profile import (
    BrokenProfile,
    ComplianceProfile,
    profile_folder_names,
    read_profile_once,
)
from vouch.settings import Settings

database_url = test_memory.database_url
postgres = test_memory.postgres
empty_database = test_memory.empty_database
users = test_memory.users

FIELDS = MUSTERFIRMA | {
    "answer_language": "de",
    "protection_level": "high",
    "not_applicable": [{"control": "1.1.1", "requirement": None, "reason": "Not ours."}],
}


@pytest.fixture(autouse=True)
def _fresh_process():
    """Each test starts as a new process: no company kept in it, nothing cached."""
    restart()
    read_profile_once.cache_clear()
    yield
    restart()


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


def test_every_field_of_a_company_setup_reaches_its_compliance_profile():
    setup = CompanySetup.model_validate(FIELDS)
    profile = compliance_profile_of(MUSTERFIRMA_ID, setup)

    own = set(CompanySetup.model_fields) - {"evidence"}
    assert profile.model_dump(mode="json", include=own) == setup.model_dump(
        mode="json", include=own
    )
    sources = profile.evidence_sources
    assert {name: list(sources[name].documents or ()) for name in sources} == {
        "policies": POLICIES,
        "records": RECORDS,
    }


def test_a_stored_company_is_checked_again_when_a_chosen_document_goes_or_comes_back(
    tmp_path, monkeypatch
):
    library = tmp_path / "library"
    shutil.copytree(SAMPLE_LIBRARY_DIR, library)
    monkeypatch.setattr(company_profiles, "SAMPLE_LIBRARY_DIR", library)
    row = StoredProfile(MUSTERFIRMA_ID, MUSTERFIRMA, "user_dev", now())
    chosen = library / "policies" / f"{POLICIES[0]}.md"
    text = chosen.read_bytes()

    assert isinstance(read_stored_once(row), ComplianceProfile)
    chosen.unlink()
    gone = read_stored_once(row)
    chosen.write_bytes(text)
    back = read_stored_once(row)

    assert isinstance(gone, BrokenProfile)
    assert POLICIES[0] in gone.problem
    assert isinstance(back, ComplianceProfile)
    assert read_stored_once(row) is back  # read again only when a document changed


def test_a_listing_of_many_companies_reads_each_once():
    """Past any fixed number of companies, a listing still reuses every profile it read."""
    rows = [StoredProfile(f"company-{n}", MUSTERFIRMA, "u", now()) for n in range(300)]
    first = [read_stored_once(row) for row in rows]

    assert all(read_stored_once(row) is profile for row, profile in zip(rows, first, strict=True))


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
        ({"country": "x\x00"}, "country"),
        ({"answer_language": "fr"}, "answer_language"),
        ({"protection_level": "medium"}, "protection_level"),
        ({"evidence": {"policies": POLICIES, "patch": ["x"]}}, "evidence.patch"),
        ({"evidence": {"policies": [], "records": []}}, "evidence"),
        ({"evidence": {"policies": ["no-such-policy"]}}, "evidence.policies"),
        ({"evidence": {"policies": ["../records/training-record"]}}, "evidence.policies"),
        ({"evidence": {"policies": ["backup-policy", "backup-policy.md"]}}, "evidence.policies"),
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
        ({"not_applicable": [{"control": "1.1.1", "reason": " "}]}, "not_applicable.0.reason"),
        ({"not_applicable": [{"control": "1.1.1", "reason": "x\x00"}]}, "not_applicable.0.reason"),
    ],
)
def test_a_refused_company_answers_422_naming_the_field_and_keeps_nothing(change, field, users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        refused = create(client, alice, MUSTERFIRMA | change)
        after = listed_ids(client, alice)

    assert refused.status_code == 422, refused.text
    assert field in refused_fields(refused), refused.text
    assert str(SAMPLE_LIBRARY_DIR) not in refused.text
    assert after == ["alpenwerk", "nordlicht"]


def refused_fields(response) -> list[str]:
    """The fields a 422 names: the request check's error locations (past `body`), or the
    profile check's `field: problem` parts of its plain detail."""
    detail = response.json()["detail"]
    if isinstance(detail, str):
        return [part.split(": ", 1)[0] for part in detail.split("; ")]
    return [".".join(str(part) for part in error["loc"][1:]) for error in detail]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"company": "Bad \ud800 AG"}, "company"),
        ({"country": "\udfff"}, "country"),
        (
            {"not_applicable": [{"control": "1.1.1", "reason": "x\ud800"}]},
            "not_applicable.0.reason",
        ),
        (
            {"not_applicable": [{"control": "1.1.1", "requirement": "\ud800", "reason": "x"}]},
            "not_applicable.0.requirement",
        ),
        ({"evidence": {"policies": ["backup\ud800"]}}, "evidence.policies.0"),
        ({"evidence": {"records": ["x\x00"]}}, "evidence.records.0"),
    ],
)
def test_text_the_database_cant_keep_is_refused_422_naming_the_field(change, field, users):
    """A lone surrogate (valid JSON, no UTF-8) or a NUL: Postgres's jsonb refuses either."""
    alice, _ = users
    body = json.dumps(MUSTERFIRMA | change)  # ASCII escapes: the surrogate survives the trip
    with TestClient(app_on(None)) as client:
        refused = client.post(
            "/api/developer/profiles",
            content=body,
            headers=alice | {"Content-Type": "application/json"},
        )
        after = listed_ids(client, alice)

    assert refused.status_code == 422, refused.text
    assert field in refused_fields(refused), refused.text
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


def test_a_compliance_leads_create_is_403_and_signed_out_401_never_reaching_the_store(clerk):
    app = clerk_app(clerk)
    app.dependency_overrides[app_company_profiles] = UnreadableStore
    client = TestClient(app, raise_server_exceptions=False)

    lead = create(client, bearer(token("user_alice")))
    signed_out = create(client, {})

    assert lead.status_code == 403
    assert signed_out.status_code == 401


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


def test_a_file_profile_with_a_stored_companys_id_wins_and_the_stored_one_is_noted(users):
    """A folder added later under a stored company's id: the file profile is listed and opened;
    the stored one is noted on the developer page."""
    alice, _ = users
    kept = company_profiles.process_company_profiles()
    assert kept.create("Nordlicht", MUSTERFIRMA, "user_dev", taken=set()) == "nordlicht"
    with TestClient(app_on(None)) as client:
        listed = client.get("/api/profiles", headers=alice).json()["profiles"]
        notes = client.get("/api/developer/settings", headers=alice).json()["broken_profiles"]
        opened = client.get("/api/overview", headers=alice | {PROFILE_HEADER: "nordlicht"})

    assert [p["id"] for p in listed] == ["alpenwerk", "nordlicht"]
    assert listed[1]["company"] != MUSTERFIRMA["company"]
    assert notes == [{"id": "nordlicht", "problem": "its id is now a file profile's"}]
    assert opened.json()["company"] == listed[1]["company"]


def test_every_folder_in_the_profiles_folder_is_a_taken_name(tmp_path):
    make_profiles(tmp_path)
    (tmp_path / "empty").mkdir()
    (tmp_path / "a-file").write_text("", encoding="utf-8")

    assert profile_folder_names(tmp_path) == {"nordlicht", "testwerk", BROKEN, "empty"}


def test_a_profiles_folder_that_cant_be_read_takes_no_name_and_is_logged(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        assert profile_folder_names(tmp_path / "missing") == set()

    assert "could not be listed" in caplog.text


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

    def remove(self, profile_id: str, removed_by: str) -> bool:
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


def test_the_company_name_limit_counts_the_trimmed_name():
    name = "A" * MAX_COMPANY_LENGTH
    setup = CompanySetup.model_validate(MUSTERFIRMA | {"company": f" {name} "})

    assert setup.company == name


def test_a_company_name_over_the_limit_says_characters():
    with pytest.raises(ValueError, match="at most 100 characters"):
        CompanySetup.model_validate(MUSTERFIRMA | {"company": "A" * (MAX_COMPANY_LENGTH + 1)})


# --- A developer removes a company made in setup (#418) ---------------------------------------


def remove(client: TestClient, headers: dict[str, str], profile_id: str = MUSTERFIRMA_ID):
    return client.delete(f"/api/developer/profiles/{profile_id}", headers=headers)


def test_a_removed_company_leaves_all_and_one_and_its_id_is_never_used_again(store):
    profile_id = store.create(FIELDS["company"], FIELDS, "user_dev", taken=set())

    assert store.remove(profile_id, "user_dev") is True
    assert store.remove(profile_id, "user_dev") is False
    assert store.remove("nobody", "user_dev") is False
    assert store.all() == []
    assert store.one(profile_id) is None
    assert store.create(FIELDS["company"], FIELDS, "user_dev", taken=set()) == f"{MUSTERFIRMA_ID}-2"
    assert [row.id for row in store.all()] == [f"{MUSTERFIRMA_ID}-2"]


def test_two_removes_of_one_company_at_once_mark_it_once(store):
    profile_id = store.create(FIELDS["company"], FIELDS, "user_dev", taken=set())
    removed: list[bool] = []
    start = threading.Barrier(2)

    def take() -> None:
        start.wait()
        removed.append(store.remove(profile_id, "user_dev"))

    threads = [threading.Thread(target=take) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(removed) == [False, True]


def test_a_developer_removes_a_company_it_leaves_every_list_and_its_id_is_never_reused(
    database_url, users, caplog
):
    alice, _ = users
    with caplog.at_level(logging.INFO), TestClient(app_on(database_url)) as client:
        assert create(client, alice).status_code == 201
        before = client.get("/api/developer/settings", headers=alice).json()["companies"]
        removed = remove(client, alice)
        listed = listed_ids(client, alice)
        again = remove(client, alice)
        made = create(client, alice).json()["id"]

    [company] = before
    assert (company["id"], company["company"]) == (MUSTERFIRMA_ID, "Musterfirma Süd GmbH")
    assert company["created_at"]
    assert removed.status_code == 200, removed.text
    assert removed.json()["companies"] == []
    assert "broken_profiles" in removed.json()
    assert f"company profile {MUSTERFIRMA_ID} removed by {alice['X-Test-User']}" in caplog.text
    assert listed == ["alpenwerk", "nordlicht"]
    assert again.status_code == 404
    assert again.json()["detail"] == "There is no such compliance profile."
    assert made == f"{MUSTERFIRMA_ID}-2"


PROFILE_ROUTES = [
    ("GET", "/api/overview", None),
    ("GET", "/api/report", None),
    ("GET", "/api/report/markdown", None),
    ("GET", "/api/report/pdf", None),
    ("GET", "/api/documents", None),
    ("GET", "/api/audit-trail", None),
    ("POST", "/api/assessments", {"control_id": "4.1.2"}),
    ("POST", "/api/profile-pass", {}),
    ("POST", "/api/chat", {"message": "Hello?", "page": "dossier"}),
]


def test_after_a_removal_every_route_under_it_is_404_and_no_list_names_it(users):
    alice, _ = users
    under = alice | {PROFILE_HEADER: MUSTERFIRMA_ID}
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        create(client, alice)
        assert client.get("/api/overview", headers=under).status_code == 200
        assert remove(client, alice).status_code == 200
        answers = {
            path: client.request(method, path, headers=under, json=body)
            for method, path, body in PROFILE_ROUTES
        }
        listed = client.get("/api/profiles", headers=alice).json()
        settings = client.get("/api/developer/settings", headers=alice).json()

    assert {path: a.status_code for path, a in answers.items()} == {
        path: 404 for _, path, _ in PROFILE_ROUTES
    }
    assert {a.json()["detail"] for a in answers.values()} == {
        "There is no such compliance profile."
    }
    assert MUSTERFIRMA_ID not in [p["id"] for p in listed["profiles"]]
    assert listed["default"] == "nordlicht"
    assert settings["broken_profiles"] == []
    assert settings["companies"] == []


def test_a_broken_company_is_listed_for_the_developer_and_can_be_removed(users):
    alice, _ = users
    stale = FIELDS | {"evidence": {"policies": ["retired-policy"]}}
    company_profiles.process_company_profiles().create("Stale GmbH", stale, "user_dev", set())
    with TestClient(app_on(None)) as client:
        before = client.get("/api/developer/settings", headers=alice).json()
        removed = remove(client, alice, "stale-gmbh").json()

    assert [c["id"] for c in before["companies"]] == ["stale-gmbh"]
    assert [b["id"] for b in before["broken_profiles"]] == ["stale-gmbh"]
    assert (removed["companies"], removed["broken_profiles"]) == ([], [])


@pytest.mark.parametrize("profile_id", ["nordlicht", "alpenwerk", "sample-library"])
def test_a_file_profile_cannot_be_removed_and_stays_listed(profile_id, users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        refused = remove(client, alice, profile_id)
        listed = listed_ids(client, alice)

    assert refused.status_code == 409
    assert refused.json()["detail"] == "Only a company made in company setup can be removed."
    assert listed == ["alpenwerk", "nordlicht"]


def test_a_broken_profiles_folder_cannot_be_removed(tmp_path, users):
    alice, _ = users
    app = create_app(Settings(profiles_dir=make_profiles(tmp_path)))
    app.dependency_overrides[current_user] = as_user
    with TestClient(app) as client:
        refused = remove(client, alice, BROKEN)

    assert refused.status_code == 409


@pytest.mark.parametrize("profile_id", ["nobody", "Not A Name", "a" * 80])
def test_an_id_no_company_has_is_404(profile_id, users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        assert remove(client, alice, profile_id).status_code == 404


def test_a_stored_company_shadowed_by_a_profile_folder_is_removed_and_the_file_profile_stays(
    users,
):
    alice, _ = users
    kept = company_profiles.process_company_profiles()
    assert kept.create("Nordlicht", MUSTERFIRMA, "user_dev", taken=set()) == "nordlicht"
    with TestClient(app_on(None)) as client:
        removed = remove(client, alice, "nordlicht")
        opened = client.get("/api/overview", headers=alice | {PROFILE_HEADER: "nordlicht"})
        listed = listed_ids(client, alice)

    assert removed.status_code == 200, removed.text
    assert removed.json()["broken_profiles"] == []
    assert kept.all() == []
    assert opened.status_code == 200
    assert opened.json()["company"] != MUSTERFIRMA["company"]
    assert listed == ["alpenwerk", "nordlicht"]


def test_a_compliance_leads_remove_is_403_and_signed_out_401_changing_nothing(clerk):
    kept = company_profiles.process_company_profiles()
    kept.create(MUSTERFIRMA["company"], MUSTERFIRMA, "user_dev", taken=set())
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    lead = remove(client, bearer(token("user_alice")))
    signed_out = remove(client, {})

    assert lead.status_code == 403
    assert signed_out.status_code == 401
    assert [row.id for row in kept.all()] == [MUSTERFIRMA_ID]


def test_with_the_database_down_a_remove_is_503_and_the_settings_have_no_companies(users, caplog):
    alice, _ = users
    down = UnreadableStore(DatabaseUnavailable("the database is unavailable: test"))
    with caplog.at_level(logging.WARNING), app_with_store(down) as client:
        removed = remove(client, alice)
        settings = client.get("/api/developer/settings", headers=alice)

    assert removed.status_code == 503
    assert removed.json()["unavailable"] == "database"
    assert settings.status_code == 200
    assert settings.json()["companies"] is None
    assert settings.json()["broken_profiles"] == []
    assert "can't be read" in caplog.text


@needs_postgres
def test_a_removed_companys_audit_trail_and_row_are_kept(postgres, users):
    alice, _ = users
    under = alice | {PROFILE_HEADER: MUSTERFIRMA_ID}
    with TestClient(app_on(postgres.url)) as client:
        create(client, alice)
        thread_id = assess(client, under)
        reviewed = client.post(
            f"/api/assessments/{thread_id}/review", json={"decision": "accept"}, headers=under
        )
        assert reviewed.status_code == 200, reviewed.text
        with postgres.connection() as conn:
            before = conn.execute(
                f"SELECT * FROM {AUDIT_TABLE} WHERE profile_id = %s ORDER BY id", (MUSTERFIRMA_ID,)
            ).fetchall()
        assert remove(client, alice).status_code == 200

    with postgres.connection() as conn:
        after = conn.execute(
            f"SELECT * FROM {AUDIT_TABLE} WHERE profile_id = %s ORDER BY id", (MUSTERFIRMA_ID,)
        ).fetchall()
        row = conn.execute(
            f"SELECT removed_at, removed_by FROM {COMPANY_PROFILES_TABLE} WHERE id = %s",
            (MUSTERFIRMA_ID,),
        ).fetchone()
    assert before
    assert after == before
    assert row is not None
    assert row["removed_at"] is not None
    assert row["removed_by"] == alice["X-Test-User"]


def test_a_removal_done_answers_200_even_when_the_settings_cannot_be_read_after_it(
    users, monkeypatch, caplog
):
    """#418 review: the removal has committed; a failure reading the settings after it must not
    say "nothing was removed". The page is told the settings are unavailable instead."""
    alice, _ = users

    def down(*args: Any) -> dict[str, Any]:
        raise DatabaseUnavailable("the database is unavailable: test")

    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        create(client, alice)
        monkeypatch.setattr(api, "_developer_page", down)
        removed = remove(client, alice)
        listed = listed_ids(client, alice)

    assert removed.status_code == 200, removed.text
    assert removed.json() == {"settings_unavailable": True}
    assert listed == ["alpenwerk", "nordlicht"]
    assert "the developer settings can't be read after removing" in caplog.text


def test_a_removal_forgets_the_companys_reads_kept_in_the_process(users):
    """#418 review: the per-profile caches drop a removed id, so they don't grow with removals."""
    alice, _ = users
    under = alice | {PROFILE_HEADER: MUSTERFIRMA_ID}
    with TestClient(app_on(None)) as client:
        create(client, alice)
        assert client.get("/api/overview", headers=under).status_code == 200
        assert MUSTERFIRMA_ID in api._paper_requirements
        assert MUSTERFIRMA_ID in read_stored_once._kept
        assert remove(client, alice).status_code == 200

    assert MUSTERFIRMA_ID not in api._paper_requirements
    assert MUSTERFIRMA_ID not in read_stored_once._kept
