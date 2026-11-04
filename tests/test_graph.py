from pathlib import Path

from vouch import ScriptedChatModel, assess, load_control

SCRIPTS = Path(__file__).parent / "scripts"


def test_gap_answer_turn_gives_gap_assessment():
    model = ScriptedChatModel.from_file(SCRIPTS / "vul_gap.json")

    result = assess(load_control("NK-VUL-01"), model)

    assert result.control_id == "NK-VUL-01"
    assert result.verdict == "gap"
    assert result.answer == "No policy covers handling technical vulnerabilities."
    assert result.sources == []


def test_sources_hold_the_cited_passages_a_search_returned():
    model = ScriptedChatModel.from_file(SCRIPTS / "enc_found.json")

    result = assess(load_control("NK-ENC-01"), model)

    assert "encryption-policy" in {s["doc_id"] for s in result.sources}
    assert all(set(s) == {"doc_id", "heading"} and s["heading"] for s in result.sources)
    assert result.warnings == []


def test_a_cited_doc_no_search_returned_is_dropped():
    model = ScriptedChatModel.from_file(SCRIPTS / "cites_unsearched.json")

    result = assess(load_control("NK-ENC-01"), model)

    assert result.sources == []  # backup-policy never searched; encryption-policy not cited


def test_missing_policy_folder_gives_gap_and_a_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSESS_POLICY_DIR", str(tmp_path / "nowhere"))
    model = ScriptedChatModel.from_file(SCRIPTS / "enc_found.json")

    result = assess(load_control("NK-ENC-01"), model)

    assert result.verdict == "gap"
    assert "source_unavailable" in result.warnings
