from vouch import ScriptedChatModel


def test_a_script_with_keys_the_format_does_not_name_still_loads(tmp_path):
    script = tmp_path / "extra.json"
    script.write_text(
        '{"gather": [{"tool": "search_policies", "args": {"query": "x"}, "id": "c1"},'
        ' {"answer": "A.", "verdict": "gap", "cited": [], "confidence": 0.9}],'
        ' "note": "kept by the author"}'
    )

    assert ScriptedChatModel.from_file(script).script.gather[-1].answer == "A."
