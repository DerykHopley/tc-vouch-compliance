import pytest
from chosen_documents import CHOSEN, SHARED_WORDS, UNCHOSEN

from vouch import search_policies
from vouch.paths import FIXTURES_DIR
from vouch.profile import NORDLICHT_PROFILE, SAMPLE_LIBRARY

POLICY_DIR = NORDLICHT_PROFILE.parent / "policies"


def test_encrypt_finds_the_encryption_standard_under_a_heading():
    passages = search_policies("encrypt", folder=POLICY_DIR)

    hits = [p for p in passages if p["doc_id"] == "encryption-standard"]
    assert hits
    assert all(p["heading"] for p in hits)


@pytest.mark.parametrize(
    ("query", "limit"),
    [("", 5), ("   ", 5), ("x" * 201, 5), ("encrypt", 0), ("encrypt", 11)],
    ids=["empty", "blank", "201-chars", "limit-0", "limit-11"],
)
@pytest.mark.parametrize("folder", ["fixture", "missing"])
def test_bad_input_is_invalid_input_even_when_the_folder_is_missing(query, limit, folder, tmp_path):
    where = POLICY_DIR if folder == "fixture" else tmp_path / "nowhere"

    result = search_policies(query, limit=limit, folder=where)

    assert result["error"] == "invalid_input"
    assert result["detail"]


def test_a_query_naming_a_path_only_returns_policy_passages():
    policy_ids = {p.stem for p in POLICY_DIR.glob("*.md")}

    passages = search_policies("../../pyproject.toml", folder=POLICY_DIR)

    assert isinstance(passages, list)
    assert {p["doc_id"] for p in passages} <= policy_ids


def test_a_missing_folder_is_source_unavailable(tmp_path):
    assert search_policies("encrypt", folder=tmp_path / "nowhere") == {
        "error": "source_unavailable"
    }


def test_only_md_files_directly_in_the_folder_are_read(tmp_path):
    (tmp_path / "notes.txt").write_text("# Notes\n\nsecret encrypt\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.md").write_text("# Nested\n\nsecret encrypt\n")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("# Outside\n\nsecret encrypt\n")
    (tmp_path / "linked.md").symlink_to(outside)
    (tmp_path / "policy.md").write_text("# Policy\n\nWe encrypt laptops.\n")

    passages = search_policies("encrypt", folder=tmp_path)

    assert [p["doc_id"] for p in passages] == ["policy"]


def test_a_file_that_is_not_utf8_is_source_unavailable(tmp_path):
    (tmp_path / "broken.md").write_bytes(b"# Broken\n\nencrypt \xff\xfe\n")

    assert search_policies("encrypt", folder=tmp_path) == {"error": "source_unavailable"}


def test_front_matter_is_never_a_passage(tmp_path):
    (tmp_path / "policy.md").write_text(
        '---\nid: policy\ntitle: Encrypt policy\nversion: "1.0"\n---\n# Top\n\nWe encrypt laptops.\n'
    )

    passages = search_policies("encrypt policy version", folder=tmp_path)

    assert passages == [{"doc_id": "policy", "heading": "Top", "text": "We encrypt laptops."}]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "﻿---\nid: policy\ntitle: Encrypt\n---\n# Top\n\nWe encrypt laptops.\n",
            [{"doc_id": "policy", "heading": "Top", "text": "We encrypt laptops."}],
        ),
        ("---\nid: policy\ntitle: Encrypt\n---", []),
    ],
    ids=["byte-order-mark", "ends-at-closing-line"],
)
def test_front_matter_is_never_a_passage_at_the_edges(text, expected, tmp_path):
    (tmp_path / "policy.md").write_text(text, encoding="utf-8")

    assert search_policies("encrypt policy", folder=tmp_path) == expected


def test_a_heading_without_a_blank_line_after_it_still_heads_its_passage(tmp_path):
    (tmp_path / "policy.md").write_text("# Top\n\nintro\n## Sub\nencrypt under sub\n")

    passages = search_policies("encrypt", folder=tmp_path)

    assert passages == [{"doc_id": "policy", "heading": "Sub", "text": "encrypt under sub"}]


def test_a_search_limited_to_chosen_documents_returns_passages_only_from_them():
    folder = FIXTURES_DIR / SAMPLE_LIBRARY / "policies"
    assert UNCHOSEN in {p["doc_id"] for p in search_policies(SHARED_WORDS, 10, folder=folder)}

    passages = search_policies(SHARED_WORDS, 10, folder=folder, documents=CHOSEN)

    assert passages
    assert {p["doc_id"] for p in passages} <= set(CHOSEN)
