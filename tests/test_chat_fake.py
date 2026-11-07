"""The chat's canned fake (vouch.chat_fake), on its own: what it answers a tool's result with."""

import test_memory
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from test_api import read_events
from test_memory import app_on

from vouch.api import PROFILE_HEADER
from vouch.chat_agent import ANSWER_REFUSED, CITE_THEM, SEARCH_EVIDENCE
from vouch.chat_fake import CHAT_REPLY, CannedChatModel
from vouch.guards import as_data
from vouch.language import mismatch, written_in

users = test_memory.users

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


def test_on_a_german_profile_the_fake_answers_in_german():
    # #233: a real model answers in the profile's language; the fake now does too, for `de`.
    answer = CannedChatModel(language="de").invoke([HumanMessage("Wie stehen wir da?")]).text

    assert mismatch(answer, "de") is None
    assert written_in(answer) == "de"


def test_on_any_other_profile_the_fake_answers_in_english():
    for language in ("en", "fr"):
        answer = CannedChatModel(language=language).invoke([HumanMessage("Hello")]).text
        assert answer == CHAT_REPLY


def test_through_the_api_on_alpenwerk_the_chat_answers_in_german(users):
    lead = users[0]
    with TestClient(app_on(None), headers={**lead, PROFILE_HEADER: "alpenwerk"}) as client:
        events = read_events(client.post("/api/chat", json={"page": "dossier", "message": "Hallo"}))

    answer = "".join(d["text"] for n, d in events if n == "token")
    assert mismatch(answer, "de") is None and answer


def refusal(text: str = "Your last answer was refused.") -> HumanMessage:
    return HumanMessage(text, name=ANSWER_REFUSED)


def test_asked_again_after_a_tool_call_the_fake_passes_the_tool_result_on_again():
    # #208: the turn keeps its content, not the canned "Thank you for your question".
    call = {"name": SEARCH_EVIDENCE, "args": {"query": "encryption"}, "id": "c1"}
    tool_words = f"Found 1 passage for “encryption”. {CITE_THEM}\n{as_data(PASSAGES)}"
    messages = [
        HumanMessage("Is there evidence of encryption?"),
        AIMessage("", tool_calls=[call]),
        ToolMessage(tool_words, tool_call_id="c1", name=SEARCH_EVIDENCE),
        refusal(),
    ]

    answer = CannedChatModel(language="de").invoke(messages).text

    assert answer.startswith("Found 1 passage for “encryption”.")
    assert '<cite doc="encryption-standard">' in answer


def test_asked_again_with_no_tool_result_the_fake_gives_its_reply_in_the_profile_language():
    messages = [HumanMessage("Hallo"), AIMessage("Hello there."), refusal()]

    answer = CannedChatModel(language="de").invoke(messages).text

    assert written_in(answer) == "de"


def test_through_the_api_on_alpenwerk_the_greeting_keeps_the_progress_words(users):
    lead = users[0]
    with TestClient(app_on(None), headers={**lead, PROFILE_HEADER: "alpenwerk"}) as client:
        events = read_events(client.post("/api/chat", json={"page": "dossier"}))

    answer = "".join(d["text"] for n, d in events if n == "token")
    assert "You have not assessed any of the 12 controls yet." in answer
    assert [n for n, _ in events][-1] == "done"
