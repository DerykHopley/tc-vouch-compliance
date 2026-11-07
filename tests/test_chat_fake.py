"""The chat's canned fake (vouch.chat_fake), on its own: what it answers a tool's result with."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vouch.chat_agent import CITE_THEM, SEARCH_EVIDENCE
from vouch.chat_fake import CannedChatModel
from vouch.guards import as_data

PASSAGES = [
    {
        "doc_id": "encryption-standard",
        "heading": "Laptops",
        "text": "Every laptop uses full-disk encryption.",
    }
]


def test_the_evidence_reply_shows_its_citations_and_no_prompt_text():
    # #292: the tool's words tell the model how to cite; the fake used to echo that to the lead.
    tool_words = f"Found 1 passage for “encryption”. {CITE_THEM}\n{as_data(PASSAGES)}"
    call = {"name": SEARCH_EVIDENCE, "args": {"query": "encryption"}, "id": "c1"}
    messages = [
        HumanMessage("Is there evidence of encryption?"),
        AIMessage("", tool_calls=[call]),
        ToolMessage(tool_words, tool_call_id="c1", name=SEARCH_EVIDENCE),
    ]

    answer = CannedChatModel().invoke(messages).text

    assert "chat prompt" not in answer
    assert CITE_THEM not in answer
    assert (
        '<cite doc="encryption-standard">Every laptop uses full-disk encryption.</cite>' in answer
    )
    assert answer.startswith("Found 1 passage for “encryption”.")
