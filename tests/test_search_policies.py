import pytest

from vouch import search_policies
from vouch.search import DEFAULT_POLICY_DIR as POLICY_DIR


def test_encrypt_finds_the_encryption_policy_under_a_heading():
    passages = search_policies("encrypt")

    hits = [p for p in passages if p["doc_id"] == "encryption-policy"]
    assert hits
    assert all(p["heading"] for p in hits)


@pytest.mark.parametrize(
    ("query", "limit"),
    [("", 5), ("   ", 5), ("x" * 201, 5), ("encrypt", 0), ("encrypt", 11)],
    ids=["empty", "blank", "201-chars", "limit-0", "limit-11"],
)
@pytest.mark.parametrize("folder", ["default", "missing"])
def test_bad_input_is_invalid_input_even_when_the_folder_is_missing(
    query, limit, folder, monkeypatch, tmp_path
):
    if folder == "missing":
        monkeypatch.setenv("ASSESS_POLICY_DIR", str(tmp_path / "nowhere"))

    result = search_policies(query, limit=limit)

    assert result["error"] == "invalid_input"
    assert result["detail"]


def test_a_query_naming_a_path_only_returns_policy_passages():
    policy_ids = {p.stem for p in POLICY_DIR.glob("*.md")}

    passages = search_policies("../../pyproject.toml")

    assert isinstance(passages, list)
    assert {p["doc_id"] for p in passages} <= policy_ids


def test_a_missing_folder_is_source_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSESS_POLICY_DIR", str(tmp_path / "nowhere"))

    assert search_policies("encrypt") == {"error": "source_unavailable"}


def test_only_md_files_directly_in_the_folder_are_read(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("# Notes\n\nsecret encrypt\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.md").write_text("# Nested\n\nsecret encrypt\n")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("# Outside\n\nsecret encrypt\n")
    (tmp_path / "linked.md").symlink_to(outside)
    (tmp_path / "policy.md").write_text("# Policy\n\nWe encrypt laptops.\n")
    monkeypatch.setenv("ASSESS_POLICY_DIR", str(tmp_path))

    passages = search_policies("encrypt")

    assert [p["doc_id"] for p in passages] == ["policy"]


def test_a_file_that_is_not_utf8_is_source_unavailable(monkeypatch, tmp_path):
    (tmp_path / "broken.md").write_bytes(b"# Broken\n\nencrypt \xff\xfe\n")
    monkeypatch.setenv("ASSESS_POLICY_DIR", str(tmp_path))

    assert search_policies("encrypt") == {"error": "source_unavailable"}


def test_a_heading_without_a_blank_line_after_it_still_heads_its_passage(monkeypatch, tmp_path):
    (tmp_path / "policy.md").write_text("# Top\n\nintro\n## Sub\nencrypt under sub\n")
    monkeypatch.setenv("ASSESS_POLICY_DIR", str(tmp_path))

    passages = search_policies("encrypt")

    assert passages == [{"doc_id": "policy", "heading": "Sub", "text": "encrypt under sub"}]
