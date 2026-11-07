"""The chat's answers go through the banned-wording check (#249), as an assessment's do: an
answer claiming an audit result ("compliant", "certified", "passed") is never shown. The model
is asked once more (a guard step says why), and an answer that claims it again ends the turn in
the chat's one formal error. On the chat's fake model, through the HTTP API."""

import test_chat_agent
from fastapi.testclient import TestClient
from pydantic import Field
from test_chat_agent import names, reply, say
from test_database import empty_database  # noqa: F401 (a fixture: postgres's)

from vouch.chat import CHAT_FAILED
from vouch.chat_agent import ASKED_AGAIN_STEP, wrong_language
from vouch.chat_fake import CannedChatModel
from vouch.language import mismatch
from vouch.model_choice import ModelChoice

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
    assert sent == ["Where are we?", "Two controls are evidenced so far.", "And next?"]


def test_the_turn_after_an_answer_refused_twice_never_reads_it(client, fake):
    fake.reply = "You passed the audit."
    thread_id = say(client, "Did we pass?")[0][1]["thread_id"]
    fake.reply = "4.1.2 is next."

    events = say(client, "And next?", thread_id=thread_id)

    assert reply(events) == "4.1.2 is next."
    sent = [str(m.content) for m in fake.prompts[-1][1:]]
    assert sent == ["Did we pass?", "And next?"]  # no refused answer, no refusal, no empty turn


# The language check (#208): a chat answer in another language than the profile's is asked for
# again once, in the profile's language, then kept with a step saying so; as an assessment's (#51).

GERMAN = "Die Richtlinie ist vorhanden und wird von der IT-Leitung jährlich geprüft."
ENGLISH = "The policy is in place and it is reviewed by the IT lead every year."


def test_the_language_check_leaves_out_what_a_citation_quotes():
    quote = (
        "The policy is reviewed and the owner is named and the scope of the policy is set for "
        "the company and the staff."
    )
    cited = f'{GERMAN} <cite doc="encryption-standard">{quote}</cite>'

    assert mismatch(cited, "de") == "en"  # read whole, the English quote outweighs the answer
    assert wrong_language(cited, "de") is None
    assert wrong_language(ENGLISH, "de") == "en"
    assert wrong_language("Ja.", "de") is None  # too short to tell


ALPENWERK = test_chat_agent.ALPENWERK
ASKED_IN_GERMAN = "Asked for the answer again: it was written in English, not German."
KEPT_IN_ENGLISH = "The answer is still written in English, not German; it was kept as it is."


def test_on_a_german_profile_an_english_answer_is_asked_again_and_the_german_one_kept(
    app, lead, fake
):
    fake.reply, fake.retry_reply = ENGLISH, GERMAN
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as client:
        events = say(client, "Wie steht es um die Richtlinie?")
        thread_id = events[0][1]["thread_id"]
        fake.reply, fake.retry_reply = "Gut.", None
        say(client, "Danke.", thread_id=thread_id)

    assert reply(events) == GERMAN
    assert guard_steps(events) == [ASKED_IN_GERMAN]
    assert ENGLISH not in str(events)
    assert names(events)[-1] == "done"
    sent = [str(m.content) for m in fake.prompts[-1][1:]]
    assert sent == ["Wie steht es um die Richtlinie?", GERMAN, "Danke."]  # no English, no refusal


def test_on_a_german_profile_an_answer_still_in_english_is_kept_with_a_step(app, lead, fake):
    fake.reply = ENGLISH
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as client:
        events = say(client, "Wie steht es um die Richtlinie?")

    assert reply(events) == ENGLISH
    assert guard_steps(events) == [ASKED_IN_GERMAN, KEPT_IN_ENGLISH]
    assert names(events)[-1] == "done"


def test_a_short_answer_and_an_english_answer_on_an_english_profile_get_no_language_step(
    app, lead, client, fake
):
    fake.reply = ENGLISH
    english = say(client, "Is there a policy?")  # Nordlicht, the default profile
    fake.reply = "Ja."
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as alpenwerk:
        short = say(alpenwerk, "Gibt es eine Richtlinie?")

    for events, answer in ((short, "Ja."), (english, ENGLISH)):
        assert reply(events) == answer
        assert guard_steps(events) == []


class InTurn(CannedChatModel):
    """The chat's fake giving `answers` one after another, as it answers or is asked again."""

    answers: list[str] = Field(default_factory=list)

    def _reply(self) -> str:
        return self.answers.pop(0)


def test_an_answer_refused_for_a_claim_then_for_its_language_still_reaches_the_lead(
    app, lead, monkeypatch
):
    claim = "Ihr ISMS ist konform, und die Richtlinie wird von der IT-Leitung geprüft."
    model = InTurn(answers=[claim, ENGLISH, GERMAN])
    monkeypatch.setattr(ModelChoice, "for_chat", lambda self, chosen, callbacks=None, **_: model)
    with TestClient(app, raise_server_exceptions=False, headers=lead | ALPENWERK) as client:
        events = say(client, "Sind wir bereit?")

    assert reply(events) == GERMAN
    assert guard_steps(events) == [ASKED_AGAIN_STEP, ASKED_IN_GERMAN]
    assert names(events)[-1] == "done"
    assert "konform" not in str(events) and ENGLISH not in str(events)
