"""What Vouch reads from each evidence document for a page (#408): `document_summary`, and the
developer route that lists the sample library for company setup's step 6."""

import logging
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_auth import FakeClerk, bearer, clerk_app, token

from vouch import api, company_profiles, report
from vouch.api import create_app
from vouch.documents import NO_REVIEW_DATE, DocumentSummary, document_summary, library_summaries
from vouch.paths import SAMPLE_LIBRARY_DIR
from vouch.settings import Settings

TODAY = date(2026, 11, 9)
PATH = "/api/developer/sample-library"
BACKUP_STALE = "approved 2024-02-12 with a yearly review, due 2025-02-12"


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_summary_gives_each_field_from_the_front_matter(tmp_path):
    path = write(
        tmp_path,
        "backup-policy",
        '---\nid: backup-policy\ntitle: Backup policy\nkind: documented\nversion: "1.0"\n'
        "approval_date: 2024-02-12\nreview_cycle: yearly\n---\n# Backup\n",
    )

    summary = document_summary(path, "policies", TODAY)

    assert summary == DocumentSummary(
        id="backup-policy",
        source="policies",
        title="Backup policy",
        kind="documented",
        version="1.0",
        approval_date="2024-02-12",
        review_cycle="yearly",
        stale_because=BACKUP_STALE,
        language=None,
    )


def test_a_current_document_has_no_stale_reason(tmp_path):
    path = write(
        tmp_path,
        "log-check-record",
        "---\ntitle: Log checks\nkind: implemented\nversion: 2026-Q3\n"
        "approval_date: 2026-10-26\nreview_cycle: yearly\n---\n",
    )

    summary = document_summary(path, "records", TODAY)

    assert (summary.kind, summary.version, summary.stale_because) == (
        "implemented",
        "2026-Q3",
        None,
    )


def test_no_title_gives_the_id_and_no_version_gives_none(tmp_path):
    path = write(
        tmp_path,
        "untitled",
        "---\nkind: documented\napproval_date: 2026-10-01\nreview_cycle: yearly\n---\n",
    )

    summary = document_summary(path, "policies", TODAY)

    assert (summary.title, summary.version) == ("untitled", None)


@pytest.mark.parametrize("version", ["1.5", "[1, 2]", "{a: 1}"])
def test_a_version_that_isnt_text_gives_none(tmp_path, version):
    path = write(tmp_path, "doc", f"---\nversion: {version}\n---\n")

    assert document_summary(path, "policies", TODAY).version is None


@pytest.mark.parametrize("approved", ["last spring", '"2026-13-01"', "[2026]"])
def test_an_approval_date_that_isnt_a_date_gives_none_and_counts_as_stale(tmp_path, approved):
    path = write(tmp_path, "doc", f"---\napproval_date: {approved}\nreview_cycle: yearly\n---\n")

    summary = document_summary(path, "policies", TODAY)

    assert (summary.approval_date, summary.stale_because) == (None, NO_REVIEW_DATE)


def test_a_review_cycle_that_isnt_text_gives_none(tmp_path):
    path = write(tmp_path, "doc", "---\nreview_cycle: [yearly]\n---\n")

    assert document_summary(path, "policies", TODAY).review_cycle is None


def test_no_front_matter_gives_the_id_the_sources_kind_and_no_review_date(tmp_path):
    path = write(tmp_path, "bare-record", "# Just a body\n")

    summary = document_summary(path, "records", TODAY)

    assert summary == DocumentSummary(
        id="bare-record",
        source="records",
        title="bare-record",
        kind="implemented",
        version=None,
        approval_date=None,
        review_cycle=None,
        stale_because=NO_REVIEW_DATE,
        language=None,
    )


# GET /api/developer/sample-library


@pytest.fixture
def clerk() -> FakeClerk:
    return FakeClerk({"user_alice": {}, "user_dev": {"role": "developer"}})


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(report, "today", lambda: TODAY)


def test_a_developer_gets_the_seventeen_documents_policies_first_each_by_title(pinned):
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert response.status_code == 200
    documents = response.json()["documents"]
    assert len(documents) == 17
    sources = [d["source"] for d in documents]
    assert sources == ["policies"] * 11 + ["records"] * 6
    policies, records = documents[:11], documents[11:]
    assert [d["title"] for d in policies] == sorted(
        (d["title"] for d in policies), key=str.casefold
    )
    assert [d["title"] for d in records] == sorted((d["title"] for d in records), key=str.casefold)
    on_disk = {p.stem for p in (SAMPLE_LIBRARY_DIR / "policies").glob("*.md")} | {
        p.stem for p in (SAMPLE_LIBRARY_DIR / "records").glob("*.md")
    }
    assert {d["id"] for d in documents} == on_disk


def test_each_listed_document_carries_its_front_matter_and_staleness(pinned):
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    documents = {d["id"]: d for d in client.get(PATH).json()["documents"]}

    assert documents["backup-policy"] == {
        "id": "backup-policy",
        "source": "policies",
        "title": "Backup policy",
        "kind": "documented",
        "version": "1.0",
        "approval_date": "2024-02-12",
        "review_cycle": "yearly",
        "stale_because": BACKUP_STALE,
        "language": "en",
        "stale": True,
    }
    # approved 2026-10-19, in the last month
    assert documents["account-changes-record"] == {
        "id": "account-changes-record",
        "source": "records",
        "title": "Account changes, third quarter of 2026",
        "kind": "implemented",
        "version": "2026-Q3",
        "approval_date": "2026-10-19",
        "review_cycle": "yearly",
        "stale_because": None,
        "language": "en",
        "stale": False,
    }


def test_only_markdown_files_directly_in_the_two_folders_are_listed(monkeypatch, tmp_path, pinned):
    (tmp_path / "policies" / "drafts").mkdir(parents=True)
    (tmp_path / "records").mkdir()
    write(tmp_path / "policies", "kept", "---\ntitle: Kept\n---\n")
    write(tmp_path / "policies" / "drafts", "draft", "---\ntitle: Draft\n---\n")
    (tmp_path / "policies" / "notes.txt").write_text("not evidence", encoding="utf-8")
    (tmp_path / "profile.yaml").write_text("company: x\n", encoding="utf-8")
    write(tmp_path, "loose", "---\ntitle: Loose\n---\n")
    monkeypatch.setattr(api, "SAMPLE_LIBRARY_DIR", tmp_path)
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert [d["id"] for d in response.json()["documents"]] == ["kept"]


def test_the_library_to_a_compliance_lead_403_and_signed_out_401(clerk: FakeClerk):
    client = TestClient(clerk_app(clerk), raise_server_exceptions=False)

    lead = client.get(PATH, headers=bearer(token("user_alice")))
    signed_out = client.get(PATH)

    assert lead.status_code == 403
    assert signed_out.status_code == 401


def test_an_unreadable_library_is_a_500_naming_no_server_path(monkeypatch, tmp_path):
    missing = tmp_path / "srv-library-gone"
    monkeypatch.setattr(api, "SAMPLE_LIBRARY_DIR", missing)
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert response.status_code == 500
    assert response.json() == {"detail": api.SAMPLE_LIBRARY_FAILED}
    assert "srv-library-gone" not in response.text
    assert str(tmp_path) not in response.text


def test_a_library_document_that_isnt_text_is_a_500_naming_no_server_path(monkeypatch, tmp_path):
    (tmp_path / "policies").mkdir()
    (tmp_path / "records").mkdir()
    (tmp_path / "policies" / "garbled.md").write_bytes(b"\xff\xfe\x00bad")
    monkeypatch.setattr(api, "SAMPLE_LIBRARY_DIR", tmp_path)
    client = TestClient(create_app(Settings()), raise_server_exceptions=False)

    response = client.get(PATH)

    assert response.status_code == 500
    assert "garbled" not in response.text


# library_summaries: the same listing rule as search and the profile pass


def library(tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    (root / "policies").mkdir()
    (root / "records").mkdir()
    write(root / "policies", "kept", "---\ntitle: Kept\nkind: documented\n---\n")
    return root


def test_a_link_pointing_out_of_the_folder_is_not_listed(tmp_path):
    root = library(tmp_path)
    outside = write(root, "secret", "---\ntitle: Outside\n---\n")
    (root / "policies" / "linked.md").symlink_to(outside)

    summaries = library_summaries(root, ("policies", "records"), TODAY)

    assert [s.id for s in summaries] == ["kept"]


def test_a_folder_swapped_for_a_link_lists_nothing(tmp_path):
    root = library(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write(elsewhere, "planted", "---\ntitle: Planted\n---\n")
    (root / "records").rmdir()
    (root / "records").symlink_to(elsewhere)

    summaries = library_summaries(root, ("policies", "records"), TODAY)

    assert [s.id for s in summaries] == ["kept"]


def test_listing_a_document_with_no_kind_logs_nothing(tmp_path, caplog):
    root = library(tmp_path)
    write(root / "records", "no-kind", "---\ntitle: No kind\n---\n")

    with caplog.at_level(logging.WARNING, logger="vouch.documents"):
        summaries = library_summaries(root, ("policies", "records"), TODAY)

    assert [s.kind for s in summaries] == ["documented", "implemented"]
    assert caplog.records == []


def test_the_listed_folders_are_the_ones_a_companys_evidence_chooses_from():
    assert set(api.LIBRARY_SOURCES) == set(company_profiles.Evidence.model_fields)


# The document's own language (#424): its front matter's, for the page's `lang`


def test_a_summary_gives_the_front_matters_language(tmp_path):
    path = write(tmp_path, "richtlinie", "---\ntitle: Richtlinie\nlanguage: de\n---\n")

    assert document_summary(path, "policies", TODAY).language == "de"


@pytest.mark.parametrize(
    "front",
    [
        "title: No language\n",
        'language: ""\n',
        "language: [de]\n",
        # Only a two-lower-case-letter code goes into an HTML `lang` (#424 review).
        'language: "de\\" onclick=x"\n',
        "language: DE\n",
        "language: deu\n",
        "language: de-AT\n",
    ],
)
def test_no_or_an_empty_language_gives_none(tmp_path, front):
    path = write(tmp_path, "doc", f"---\n{front}---\n")

    assert document_summary(path, "policies", TODAY).language is None
