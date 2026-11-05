"""The chat answers questions about the evidence (#46): `explain_control` and `search_evidence`,
exact quotes checked against the documents, and a refusal to sign off for the lead. Through the
chat entry point (the HTTP API), on the chat's fake model and the bundled assessment script: no
key, no paid call. Each case runs in memory and on Postgres (#38's fixtures)."""

import pytest
import test_memory
from fastapi.testclient import TestClient
from test_chat_agent import assess_and_review, names, reply, say
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)
from test_memory import app_on, as_user
from test_middleware import copy_profile

from vouch.api import create_app, current_user
from vouch.chat_agent import CHAT_TOOLS
from vouch.chat_fake import CannedChatModel
from vouch.model_choice import ModelChoice
from vouch.profile import read_profile_once
from vouch.settings import Settings

database_url = test_memory.database_url
postgres = test_memory.postgres
users = test_memory.users


@pytest.fixture
def lead(users) -> dict[str, str]:
    return users[0]


@pytest.fixture
def client(database_url, lead):
    with TestClient(app_on(database_url), raise_server_exceptions=False, headers=lead) as client:
        yield client


@pytest.fixture
def fake(monkeypatch) -> CannedChatModel:
    """The chat's fake, kept, so a test can set its reply and read the prompts it was sent."""
    model = CannedChatModel()
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None: model)
    return model


def assessed(client, control_id: str) -> dict:
    """The control's newest assessment, as the API gives it."""
    listed = client.get("/api/assessments").json()["assessments"]
    newest = next(a for a in listed if a["control_id"] == control_id)
    return client.get(f"/api/assessments/{newest['thread_id']}").json()["assessment"]


def guard_steps(events) -> list[str]:
    return [data["text"] for name, data in events if name == "step" and data["node"] == "guard"]


# explain_control: what's missing, with the quotes


def test_whats_missing_on_the_working_paper_names_each_requirement_not_evidenced_and_quotes(
    client,
):
    assess_and_review(client, "5.1.1")
    assessment = assessed(client, "5.1.1")
    missing = [r for r in assessment["requirements"] if r["verdict"] != "evidenced"]
    quotes = [(c["quote"], c["doc_id"]) for r in assessment["requirements"] for c in r["citations"]]
    assert missing and quotes  # the bundled script leaves something missing, and quotes

    events = say(client, "What's missing for 5.1.1?", page="paper", control_id="5.1.1")

    text = reply(events)
    for requirement in missing:
        assert requirement["id"] in text and requirement["text"] in text
    for quote, doc_id in quotes:
        assert f"“{quote}” [{doc_id}]" in text
    assert guard_steps(events) == []  # every quote passed the check
    assert ("suggestion", {"control_id": "5.1.1", "action": "review"}) in events
    assert names(events)[-1] == "done"


def test_whats_missing_here_means_the_control_of_the_working_paper_the_lead_is_on(client, fake):
    assess_and_review(client, "5.1.1")

    events = say(client, "What's missing here?", page="paper", control_id="5.1.1")

    assert "Control 5.1.1" in reply(events)
    tool_reply = fake.prompts[1][-1]
    assert tool_reply.type == "tool" and tool_reply.name == "explain_control"


def test_a_control_not_assessed_yet_is_said_so_with_its_requirements_and_the_next_step(client):
    events = say(client, "What's missing for 1.1.1?")

    text = reply(events)
    assert "Control 1.1.1" in text and "has not been assessed yet" in text
    assert "The next step is to assess control 1.1.1." in text
    assert "[" not in text  # no quotes cited: nothing was read
    assert ("suggestion", {"control_id": "1.1.1", "action": "assess"}) in events


# Signing off is the lead's


@pytest.mark.parametrize("message", ["Accept 4.1.2", "Please reject 4.1.2", "Override 4.1.2 M1"])
def test_asked_to_sign_off_the_chat_explains_it_is_the_leads_links_the_paper_and_changes_nothing(
    client, message
):
    assess_and_review(client, "4.1.2")
    before = assessed(client, "4.1.2")

    events = say(client, message, page="dossier")

    text = reply(events)
    assert "signing off is yours" in text
    assert "control 4.1.2" in text and "its working paper" in text
    assert ("suggestion", {"control_id": "4.1.2", "action": "review"}) in events  # the link
    assert names(events)[-1] == "done"
    # No review was made: the verdict waits as it did, and the audit trail has no entry.
    assert assessed(client, "4.1.2") == before
    assert before["status"] == "waiting-for-review" and before["review"] is None
    assert client.get("/api/audit-trail").json()["entries"] == []


def test_the_chat_agent_is_told_it_never_signs_off_and_has_no_tool_that_could(client, fake):
    say(client, "Accept 4.1.2")

    prompt = fake.prompts[0][0].content
    assert "You never accept, reject or override a verdict" in prompt
    assert sorted(tool["function"]["name"] for tool in CHAT_TOOLS) == [
        "explain_control",
        "progress",
        "search_evidence",
        "start_assessment",
    ]


# search_evidence: the policies and records the profile allows


def test_a_question_with_evidence_is_answered_with_quotes_from_the_documents(client):
    events = say(client, "Is there evidence of full-disk encryption?")

    text = reply(events)
    assert "- “Every company laptop uses full-disk encryption with AES-256." in text
    assert "[encryption-standard]" in text
    assert guard_steps(events) == []  # each quote passed the check
    searched = [data["text"] for name, data in events if name == "step"]
    assert any(s.startswith("Searched the policies for “full-disk encryption”") for s in searched)
    assert any(s.startswith("Searched the records for “full-disk encryption”") for s in searched)


def say_allowing(tmp_path, lead, message: str, **sources: str):
    """`message` said to an app whose Nordlicht profile allows only these sources."""
    copy_profile(tmp_path, **sources)
    read_profile_once.cache_clear()
    app = create_app(Settings(profiles_dir=tmp_path))
    app.dependency_overrides[current_user] = as_user
    with TestClient(app, raise_server_exceptions=False, headers=lead) as client:
        return say(client, message)


def test_only_the_sources_the_profile_allows_are_searched(tmp_path, lead):
    message = "Is there evidence of an access review?"
    events = say_allowing(tmp_path, lead, message, policies="policies", patch="patch")

    searched = [data["text"] for name, data in events if name == "step"]
    assert [s.split(" for ")[0] for s in searched] == ["Searched the policies"]
    assert "[access-review-record]" not in reply(events)  # a record: not allowed


def test_a_profile_allowing_neither_policies_nor_records_has_nothing_to_search(tmp_path, lead):
    events = say_allowing(tmp_path, lead, "Is there evidence of encryption?", patch="patch")

    assert reply(events) == "This compliance profile allows no policies or records to be searched."


def test_a_question_with_no_evidence_gets_the_no_evidence_reply_and_no_invented_answer(client):
    events = say(client, "Is there evidence of zeppelin xylophones?")

    assert reply(events) == "I found no evidence for that in your documents."
    assert names(events)[-1] == "done"


# The quote check on the answer


INVENTED = "“Every laptop is encrypted twice a day by the night shift.” [encryption-standard]"
REAL = "“Every company laptop uses full-disk encryption with AES-256.” [encryption-standard]"


def test_an_invented_quote_in_a_chat_answer_is_dropped_and_a_real_one_kept(client, fake):
    fake.reply = f"The encryption standard says {INVENTED}, and {REAL}."

    events = say(client, "Tell me about laptops.")

    assert reply(events) == f"The encryption standard says [quote removed], and {REAL}."
    assert guard_steps(events) == [
        "Dropped a quote from encryption-standard: the quote isn't in that document."
    ]


def test_an_invented_quote_split_across_the_streamed_pieces_is_still_dropped(client, fake):
    fake.chunks = [
        "It says “Every laptop is enc",
        "rypted twice a day by the night shift.” [encr",
        "yption-standard] today.",
    ]

    events = say(client, "Tell me about laptops.")

    assert reply(events) == "It says [quote removed] today."
    assert len(guard_steps(events)) == 1


def test_a_quote_from_a_document_outside_the_profile_is_dropped(client, fake):
    fake.reply = (
        "It says “Every company laptop uses full-disk encryption with AES-256.” [secret-plan]."
    )

    events = say(client, "Tell me about laptops.")

    assert reply(events) == "It says [quote removed]."
    assert guard_steps(events) == ["Dropped a quote from secret-plan: no such document."]


def test_the_chat_thread_keeps_the_answer_with_the_invented_quote_dropped(client, fake):
    fake.reply = f"It says {INVENTED}."
    first = say(client, "Tell me about laptops.")
    fake.reply = "Noted."

    say(client, "Thank you.", thread_id=first[0][1]["thread_id"])

    kept = [m.content for m in fake.prompts[-1] if m.type == "ai"]
    assert kept == ["It says [quote removed]."]


def test_a_quoted_phrase_that_cites_no_document_is_left_as_written(client, fake):
    fake.reply = "The control asks “whether information security is managed” in general."

    events = say(client, "Tell me about it.")

    assert reply(events) == fake.reply
    assert guard_steps(events) == []
