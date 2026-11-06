"""The chat's answers go through the banned-wording check (#249), as an assessment's do: an
answer claiming an audit result ("compliant", "certified", "passed") is never shown. The model
is asked once more (a guard step says why), and an answer that claims it again ends the turn in
the chat's one formal error. On the chat's fake model, through the HTTP API."""

import test_chat_agent
from test_chat_agent import names, reply, say
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)

from vouch.chat import CHAT_FAILED

client, app, lead, users = (
    test_chat_agent.client,
    test_chat_agent.app,
    test_chat_agent.lead,
    test_chat_agent.users,
)
database_url, postgres, fake = (
    test_chat_agent.database_url,
    test_chat_agent.postgres,
    test_chat_agent.fake,
)


def guard_steps(events) -> list[str]:
    return [d["text"] for n, d in events if n == "step" and d["node"] == "guard"]


def test_an_answer_claiming_an_audit_result_is_asked_again_and_never_shown(client, fake):
    fake.reply = "Your ISMS is compliant with every control."
    fake.retry_reply = "Your ISMS has evidence for 4.1.2; 5.1.1 still needs a record."

    events = say(client, "Are we ready?")

    assert reply(events) == fake.retry_reply
    assert "compliant" not in str(events)  # neither shown nor named in a step
    assert any("audit result" in step for step in guard_steps(events))
    assert names(events)[-1] == "done"


def test_an_answer_that_claims_it_again_ends_the_turn_in_the_formal_error(client, fake):
    fake.reply = "You passed the audit."

    events = say(client, "Did we pass?")

    assert names(events)[-1] == "error"
    assert events[-1][1]["message"] == CHAT_FAILED
    assert reply(events) == ""
    assert "passed the audit" not in str(events)


def test_a_negated_claim_is_shown_as_written(client, fake):
    fake.reply = "Your ISMS is not yet compliant: 5.1.1 still needs a record."

    events = say(client, "Are we ready?")

    assert reply(events) == fake.reply
    assert guard_steps(events) == []


def test_the_next_turn_goes_on_after_a_refused_answer(client, fake):
    fake.reply = "Certified, all of it."
    fake.retry_reply = "Two controls are evidenced so far."
    thread_id = say(client, "Where are we?")[0][1]["thread_id"]
    fake.reply, fake.retry_reply = "And 4.1.2 is next.", None

    events = say(client, "And next?", thread_id=thread_id)

    assert reply(events) == "And 4.1.2 is next."
    sent = [str(m.content) for m in fake.prompts[-1][1:]]
    assert not any("Certified, all of it." in m for m in sent)  # the refused answer isn't kept
