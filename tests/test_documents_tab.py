"""The Documents tab (#424): each evidence document a compliance profile uses, with the controls
whose newest assessment cites it (`profile_documents`), the tab's own words in the profile's
language (`document_words`), and `GET /api/documents`, for every signed-in user."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import test_memory
from company_setup import MUSTERFIRMA, MUSTERFIRMA_ID
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_auth import FakeClerk, bearer, clerk_app, token
from test_database import UNREACHABLE_URL
from test_dossier import quote, seeded
from test_memory import app_on, restart
from test_report import every_word, reviewed, under

from vouch import api, documents_tab, isa, report
from vouch.database import Database
from vouch.documents_tab import DOCUMENT_WORDS, document_words
from vouch.documents_tab import profile_documents as documents
from vouch.graph import Assessment
from vouch.isa import load_base_framework
from vouch.language import audit_claim
from vouch.paths import FIXTURES_DIR
from vouch.profile import load_named_profile, load_stored_profile

users = test_memory.users

TODAY = date(2026, 11, 9)
PATH = "/api/documents"
NORDLICHT = load_named_profile("nordlicht")
CONTROLS = list(load_base_framework().controls)
FIELDS = {k: v for k, v in MUSTERFIRMA.items() if k != "evidence"} | {"not_applicable": []}
# Two of the sample library's documents, each by title the other way round.
CHOSEN = {"policies": ["logging-standard", "backup-policy"]}


def chosen_company():
    return load_stored_profile("musterfirma", FIELDS, CHOSEN)


def ids(listed: list[dict[str, Any]]) -> list[str]:
    return [d["id"] for d in listed]


def by_id(listed: list[dict[str, Any]], doc_id: str) -> dict[str, Any]:
    return next(d for d in listed if d["id"] == doc_id)


def profile_documents(*args: Any) -> list[dict[str, Any]]:
    """profile_documents' rows as the API sends them."""
    return [d.as_dict() for d in documents(*args)]


def citing(control_id: str, *doc_ids: str, status: str = "accepted") -> Assessment:
    citations = tuple(quote(d, f"From {d}.") for d in doc_ids)
    return seeded(control_id, "evidenced", status, citations=citations)  # type: ignore[arg-type]


# --- profile_documents: which documents, in which order -----------------------------------------


def test_a_setup_made_company_lists_only_its_chosen_documents_by_title():
    listed = profile_documents(chosen_company(), CONTROLS, {}, TODAY)

    assert ids(listed) == ["backup-policy", "logging-standard"]
    assert {d["source"] for d in listed} == {"policies"}


def test_a_whole_folder_source_lists_every_document_in_the_profiles_source_order():
    listed = profile_documents(NORDLICHT, CONTROLS, {}, TODAY)

    sources = [d["source"] for d in listed]
    assert sources == sorted(sources, key=["policies", "records", "patch"].index)
    for name, source in NORDLICHT.evidence_sources.items():
        on_disk = {p.stem for p in source.folder.glob("*.md")}
        assert {d["id"] for d in listed if d["source"] == name} == on_disk
        titles = [d["title"] for d in listed if d["source"] == name]
        assert titles == sorted(titles, key=str.casefold)


def test_each_document_carries_its_summary_staleness_and_language():
    listed = profile_documents(chosen_company(), CONTROLS, {}, TODAY)

    backup = by_id(listed, "backup-policy")
    assert backup["title"] == "Backup policy"
    assert (backup["version"], backup["approval_date"], backup["review_cycle"]) == (
        "1.0",
        "2024-02-12",
        "yearly",
    )
    assert (backup["stale"], backup["language"], backup["cited_by"]) == (True, "en", [])


# --- cited_by ----------------------------------------------------------------------------------


def test_cited_by_names_each_citing_control_in_control_order():
    latest = {
        "4.1.2": citing("4.1.2", "authentication-standard"),
        "1.1.1": citing("1.1.1", "authentication-standard", "information-security-policy"),
    }

    listed = profile_documents(NORDLICHT, CONTROLS, latest, TODAY)

    assert by_id(listed, "authentication-standard")["cited_by"] == ["1.1.1", "4.1.2"]
    assert by_id(listed, "information-security-policy")["cited_by"] == ["1.1.1"]
    assert by_id(listed, "logging-standard")["cited_by"] == []


def test_a_control_citing_a_document_twice_is_named_once():
    latest = {"4.1.2": citing("4.1.2", "authentication-standard", "authentication-standard")}

    listed = profile_documents(NORDLICHT, CONTROLS, latest, TODAY)

    assert by_id(listed, "authentication-standard")["cited_by"] == ["4.1.2"]


def test_a_rejected_newest_assessment_cites_nothing():
    latest = {"4.1.2": citing("4.1.2", "authentication-standard", status="rejected")}

    listed = profile_documents(NORDLICHT, CONTROLS, latest, TODAY)

    assert by_id(listed, "authentication-standard")["cited_by"] == []


def test_an_assessment_waiting_for_review_counts():
    latest = {"4.1.2": citing("4.1.2", "authentication-standard", status="waiting-for-review")}

    listed = profile_documents(NORDLICHT, CONTROLS, latest, TODAY)

    assert by_id(listed, "authentication-standard")["cited_by"] == ["4.1.2"]


def test_an_assessment_of_a_control_outside_the_controls_cites_nothing():
    latest = {"9.9.9": citing("9.9.9", "authentication-standard")}

    listed = profile_documents(NORDLICHT, CONTROLS, latest, TODAY)

    assert by_id(listed, "authentication-standard")["cited_by"] == []


def test_a_citation_of_a_document_the_profile_doesnt_list_adds_no_row():
    latest = {"4.1.2": citing("4.1.2", "information-security-policy", "backup-policy")}

    listed = profile_documents(chosen_company(), CONTROLS, latest, TODAY)

    assert ids(listed) == ["backup-policy", "logging-standard"]
    assert by_id(listed, "backup-policy")["cited_by"] == ["4.1.2"]


# --- The tab's words -------------------------------------------------------------------------


def test_german_gets_the_german_words_and_english_or_an_unknown_language_the_english():
    assert document_words("de") == ("de", DOCUMENT_WORDS["de"])
    assert document_words("en") == ("en", DOCUMENT_WORDS["en"])
    assert document_words("lt") == ("en", DOCUMENT_WORDS["en"])


def test_the_language_is_chosen_from_the_tabs_own_tables(monkeypatch):
    monkeypatch.setattr(documents_tab, "DOCUMENT_WORDS", {"en": DOCUMENT_WORDS["en"]})

    assert document_words("de") == ("en", DOCUMENT_WORDS["en"])


def test_both_tables_have_the_same_keys():
    def shape(words: Any) -> Any:
        return {k: shape(v) for k, v in words.items()} if isinstance(words, dict) else None

    assert shape(DOCUMENT_WORDS["de"]) == shape(DOCUMENT_WORDS["en"])


def test_every_review_cycle_has_a_word():
    from vouch.documents import CYCLE_MONTHS

    for words in DOCUMENT_WORDS.values():
        assert set(words["cycles"]) == set(CYCLE_MONTHS)


@pytest.mark.parametrize("language", ["en", "de"])
def test_no_word_claims_an_audit_result(language):
    claims = {w: audit_claim(w) for w in every_word(DOCUMENT_WORDS[language])}

    assert {w: c for w, c in claims.items() if c} == {}


# --- GET /api/documents ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_process():
    restart()
    yield
    restart()


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(report, "today", lambda: TODAY)


def documents_of(client: TestClient, user: dict[str, str]) -> dict[str, Any]:
    response = client.get(PATH, headers=user)
    assert response.status_code == 200, response.text
    return response.json()


def test_nordlicht_after_4_1_2_lists_the_authentication_standard_cited_by_it(users, pinned):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        reviewed(client, alice)  # 4.1.2, on the fake model
        shown = documents_of(client, alice)

    standard = by_id(shown["documents"], "authentication-standard")
    assert standard["cited_by"] == ["4.1.2"]
    assert (standard["version"], standard["approval_date"], standard["review_cycle"]) == (
        "3.1",
        "2026-04-08",
        "two-yearly",
    )
    assert standard["language"] == "en"
    assert shown["words_language"] == "en"
    assert shown["words"] == DOCUMENT_WORDS["en"]


def test_a_setup_made_company_lists_only_its_chosen_documents(users, pinned):
    alice, _ = users
    body = MUSTERFIRMA | {"evidence": {"policies": ["backup-policy"], "records": []}}
    with TestClient(app_on(None)) as client:
        made = client.post("/api/developer/profiles", json=body, headers=alice)
        assert made.status_code == 201, made.text
        shown = documents_of(client, under(alice, MUSTERFIRMA_ID))

    assert ids(shown["documents"]) == ["backup-policy"]
    assert shown["documents"][0]["stale"] is True


def test_under_alpenwerk_the_words_are_german(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        shown = documents_of(client, under(alice, "alpenwerk"))

    assert shown["words_language"] == "de"
    assert shown["words"]["title"] == "Dokumente"
    assert {d["language"] for d in shown["documents"]} == {"de"}


def test_the_pages_lang_is_the_language_of_the_words_sent(users, monkeypatch):
    monkeypatch.setattr(documents_tab, "DOCUMENT_WORDS", {"en": DOCUMENT_WORDS["en"]})
    alice, _ = users
    with TestClient(app_on(None)) as client:
        shown = documents_of(client, under(alice, "alpenwerk"))

    assert (shown["words_language"], shown["words"]) == ("en", DOCUMENT_WORDS["en"])


def test_a_compliance_lead_gets_the_documents_and_signed_out_401():
    client = TestClient(clerk_app(FakeClerk({"user_alice": {}})), raise_server_exceptions=False)

    lead = client.get(PATH, headers=bearer(token("user_alice")))
    signed_out = client.get(PATH)

    assert lead.status_code == 200, lead.text
    assert ids(lead.json()["documents"])
    assert signed_out.status_code == 401


def test_an_unknown_profile_is_a_404(users):
    alice, _ = users
    with TestClient(app_on(None)) as client:
        response = client.get(PATH, headers=under(alice, "nowhere"))

    assert response.status_code == 404


def test_an_unreadable_folder_is_a_500_naming_no_server_path(users, monkeypatch):
    alice, _ = users

    def unreadable(folder: Path) -> list[Path]:
        raise PermissionError(13, "Permission denied", str(folder))

    monkeypatch.setattr(documents_tab, "document_files", unreadable)
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        response = client.get(PATH, headers=alice)

    assert response.status_code == 500
    assert response.json() == {"detail": api.DOCUMENTS_FAILED}
    assert str(FIXTURES_DIR) not in response.text


def test_a_document_that_isnt_text_is_a_500_naming_no_server_path(users, monkeypatch, tmp_path):
    alice, _ = users
    (tmp_path / "garbled.md").write_bytes(b"\xff\xfe\x00bad")
    monkeypatch.setattr(documents_tab, "document_files", lambda folder: [tmp_path / "garbled.md"])
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        response = client.get(PATH, headers=alice)

    assert response.status_code == 500
    assert response.json() == {"detail": api.DOCUMENTS_FAILED}
    assert "garbled" not in response.text


def test_a_broken_german_edition_is_a_plain_500(users, monkeypatch, tmp_path):
    alice, _ = users
    alpenwerk = under(alice, "alpenwerk")
    broken = tmp_path / "controls-de.yaml"
    broken.write_text("controls: [\n", encoding="utf-8")
    with TestClient(app_on(None), raise_server_exceptions=False) as client:
        documents_of(client, alpenwerk)  # the profile is read while its edition is whole
        monkeypatch.setattr(isa, "CONTROLS_DE_FILE", broken)
        response = client.get(PATH, headers=alpenwerk)

    assert response.status_code == 500
    assert response.json() == {"detail": api.DOCUMENTS_FAILED}
    assert str(tmp_path) not in response.text


def test_with_the_database_down_the_documents_answer_503(users):
    alice, _ = users
    app = app_on(SecretStr(UNREACHABLE_URL))
    app.state.database = Database(SecretStr(UNREACHABLE_URL), timeout=0.3)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(PATH, headers=alice)

    assert response.status_code == 503
    assert response.json()["unavailable"] == "database"
