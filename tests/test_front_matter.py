import pytest

from vouch.front_matter import split_front_matter


def test_front_matter_and_body_are_split():
    assert split_front_matter("---\nid: a\nversion: '1.0'\n---\n# Top\n\ntext\n") == (
        {"id": "a", "version": "1.0"},
        "# Top\n\ntext\n",
    )


def test_a_byte_order_mark_before_the_front_matter_is_dropped():
    assert split_front_matter("﻿---\nid: a\n---\n# Top\n") == ({"id": "a"}, "# Top\n")


@pytest.mark.parametrize("text", ["---\nid: a\n---", "---\nid: a\n---\n"])
def test_a_file_that_ends_at_the_closing_line_has_an_empty_body(text):
    assert split_front_matter(text) == ({"id": "a"}, "")


def test_windows_line_endings_still_split():
    assert split_front_matter("---\r\nid: a\r\n---\r\n# Top\r\n") == ({"id": "a"}, "# Top\r\n")


@pytest.mark.parametrize(
    "text",
    ["# Top\n\ntext\n", "---\nid: a\n# never closed\n", "", "--- \nid: a\n---\n"],
    ids=["none", "unclosed", "empty", "not-a-bare-opening-line"],
)
def test_without_a_closed_block_the_whole_text_is_body(text):
    assert split_front_matter(text) == (None, text)


@pytest.mark.parametrize(
    "head",
    ["id: [unclosed", "- a list", "", "just words"],
    ids=["bad-yaml", "list", "empty", "scalar"],
)
def test_a_block_that_isnt_a_mapping_gives_no_data_but_is_still_cut_from_the_body(head):
    assert split_front_matter(f"---\n{head}\n---\n# Top\n") == (None, "# Top\n")
