"""The chat's fake model (#44): a canned formal answer, streamed, never touching the network.

Offline, and in tests, the chat runs on it (vouch.model_choice.ModelChoice.for_chat). Given the
chat agent's tools (#45, #46), it calls one as a model would: `start_assessment` for "Assess
<id>", `progress` for the greeting or "where do I stand", `explain_control` for "What's missing
for <id>?" (the page's control when none is named) or with `sign_off` for "Accept <id>", and
`search_evidence` for "Is there evidence of <words>?", and `assessed_controls` for a
question across controls ("Which controls have documented evidence only?", #210), and
`compare_rounds` for what changed since a round ("What improved since Q2 2026?", #581). Then it
passes the tool's words on as its answer, each passage of evidence cited as the chat prompt asks:
<cite doc="doc_id">its first line</cite>. On a German profile it says them in German
(german_answer, #330), as a real model answers in the profile's language.
"""

import json
import re
from collections.abc import Callable, Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, Field

from vouch.chat_agent import (
    ASSESSED_CONTROLS,
    CITE_THEM,
    COMPARE_ROUNDS,
    EXPLAIN_CONTROL,
    GREETING_REQUEST,
    NO_EVIDENCE,
    NO_SEARCH_ALLOWED,
    PROGRESS,
    SEARCH_EVIDENCE,
    START_ASSESSMENT,
    is_lead,
    is_refusal,
)
from vouch.chat_citations import CITE
from vouch.fake_model import _usage

CHAT_REPLY = (
    "Thank you for your question. Please ask what is missing for a control, or whether there "
    "is evidence of something in your documents."
)
CHAT_REPLIES = {
    "de": (
        "Vielen Dank für Ihre Frage. Fragen Sie bitte, was für eine Kontrolle noch fehlt, oder "
        "ob es in Ihren Dokumenten Nachweise für etwas gibt."
    ),
}
"""CHAT_REPLY in a profile's answer language, where the fake has one (#233): a real model answers
in it; any other language gets CHAT_REPLY."""
"""The chat's fake answer: formal, in English, whatever else is asked."""
_ID = r"(\d+(?:\.\d+)+)"
ASSESS = re.compile(r"\bassess\s+(?:control\s+)?(\S+)", re.IGNORECASE)
WHERE = re.compile(r"where do I stand|my progress", re.IGNORECASE)
SIGN_OFF = re.compile(rf"\b(accept|reject|override|approve|sign off)\b\D*{_ID}", re.IGNORECASE)
MISSING = re.compile(rf"\b(?:missing|explain)\b(?:\D*{_ID})?", re.IGNORECASE)
EVIDENCE = re.compile(r"\bevidence (?:of|for|about|on) (.+?)[?.!]*$", re.IGNORECASE)
SINCE_ROUND = re.compile(
    r"\b(?:improved|better|worse|changed)\b.*?\bsince\s+(?:the\s+)?(.+?)[?.!]*$", re.IGNORECASE
)
LAST_ROUND = re.compile(r"^(?:last|previous) (?:review )?round$", re.IGNORECASE)
ACROSS_CONTROLS = re.compile(r"\bwhich (?:controls|musts|requirements)\b", re.IGNORECASE)
PAGE_CONTROL = re.compile(rf"VDA ISA control {_ID}:")
_EVIDENCE_DATA = re.compile(r"<evidence>\n(.*?)\n</evidence>", re.DOTALL)
_FINDINGS_DATA = re.compile(r"<findings>\n(.*?)\n</findings>", re.DOTALL)
_LIST_MARK = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _words(text: str) -> list[str]:
    return re.findall(r"\s*\S+", text)


def _cited(tool_words: str) -> str:
    """A tool's words with its evidence data given as citations: each passage's first line,
    in a cite naming its document id."""

    def cite(found: re.Match[str]) -> str:
        lines = []
        for passage in json.loads(found.group(1)):
            if "text" not in passage:
                continue
            first = _LIST_MARK.sub("", passage["text"].splitlines()[0].strip())
            label = f"{passage['requirement']}: " if "requirement" in passage else ""
            lines.append(f'- {label}<cite doc="{passage["doc_id"]}">{first}</cite>')
        return "\n".join(lines)

    def findings(found: re.Match[str]) -> str:
        return "\n".join(
            f"- {f['requirement']} ({f['column']}): {f['text']} "
            f"{f['verdict'].capitalize()}: {f['reason']}"
            for f in json.loads(found.group(1))
        )

    words = tool_words.replace(
        f" {CITE_THEM}", ""
    )  # the model's instruction, not the lead's (#292)
    return _FINDINGS_DATA.sub(findings, _EVIDENCE_DATA.sub(cite, words))


_MONTHS = {
    "January": "Januar",
    "February": "Februar",
    "March": "März",
    "April": "April",
    "May": "Mai",
    "June": "Juni",
    "July": "Juli",
    "August": "August",
    "September": "September",
    "October": "Oktober",
    "November": "November",
    "December": "Dezember",
}
_STATUSES = {  # vouch.chat_agent._STATUS_WORDS: (said of one control, of several)
    "accepted": ("akzeptiert", "akzeptiert"),
    "rejected": ("abgelehnt", "abgelehnt"),
    "proposed and waiting for your review": (
        "vorgeschlagen und wartet auf Ihre Prüfung",
        "vorgeschlagen und warten auf Ihre Prüfung",
    ),
    "not assessed": ("nicht bewertet", "nicht bewertet"),
}
_STATUS = "(" + "|".join(_STATUSES) + ")"
_SIGN_OFFS = {"accept": "akzeptieren", "reject": "ablehnen", "override": "überschreiben"}
_KINDS = {"documented": "dokumentiert", "implemented": "umgesetzt"}
_KIND_LIST = r"(?:documented|implemented)(?: and (?:documented|implemented))?"
_SOURCES = {"the policies": "die Richtlinien", "the records": "die Aufzeichnungen"}
_QUOTED = r"(“[^”\n]*”)"
# The roll-up's explanation as the working paper's rollUpWords says it on `de` (review.ts).
_NOUNS = {
    "must requirement": ("Muss-Anforderung", "Muss-Anforderungen"),
    "must, protection-level or overlay requirement": (
        "Muss-, Schutzbedarfs- oder Overlay-Anforderung",
        "Muss-, Schutzbedarfs- oder Overlay-Anforderungen",
    ),
}
_NOUN = "(" + "|".join(re.escape(n) for n in _NOUNS) + ")"
_SHOULDS = "should requirements count towards the should score only"
_SOLL = "Soll-Anforderungen zählen nur zur Soll-Quote"
_LABELS = {  # assessed_controls_words' closing labels
    "Documented evidence only": "Nur dokumentierte Nachweise",
    "Stale evidence": "Nachweise mit überfälliger Prüfung",
    "Musts not evidenced": "Nicht nachgewiesene Muss-Anforderungen",
    "Not assessed yet": "Noch nicht bewertet",
}

Say = str | Callable[[re.Match[str]], str]


def _line(shape: str) -> re.Pattern[str]:
    """A shape that is a whole line of the tool's words."""
    return re.compile(f"^{shape}$", re.MULTILINE)


_COUNT = rf"\d+ (?:is|are) {_STATUS}"
_COUNTS = re.compile(rf"{_COUNT}(?:,(?: and)? {_COUNT})*")


def _counts(found: re.Match[str]) -> str:
    """progress_words' count of controls in each review state; any other sentence of that
    shape (a document's, say) as it is."""
    if not _COUNTS.fullmatch(found.group(2)):
        return found.group(0)
    parts: list[str] = []
    for n, status in re.findall(rf"(\d+) (?:is|are) {_STATUS}", found.group(2)):
        verb = "ist" if n == "1" else "sind"
        words = _STATUSES[status][n != "1"]
        parts.append(f"{n} {verb} {words}" if parts else f"{verb} {n} {words}")
    listed = ", ".join(parts[:-1]) + ", und " + parts[-1] if len(parts) > 1 else parts[0]
    return f"Von den {found.group(1)} Kontrollen {listed}."


def _rests_on(found: re.Match[str]) -> str:
    """What a control's counted requirements rest on, in assessed_controls_words."""
    said = "die gezählten Anforderungen stützen sich "
    if found.group(2) is None:
        return said + "auf keine zitierten Nachweise."
    named = " und ".join(f"{_KINDS[k]}e" for k in found.group(2).split(" and "))
    return said + ("nur " if found.group(1) else "") + f"auf {named} Nachweise."


def _requirement(found: re.Match[str]) -> str:
    """A requirement in brief, in assessed_controls_words: its evidence kinds and staleness."""
    kinds = found.group(2)
    named = ", " + " und ".join(_KINDS[k] for k in kinds.split(" and ")) if kinds else ""
    return found.group(1) + named + (", Prüfung überfällig" if found.group(3) else "")


def _unread(found: re.Match[str]) -> str:
    """search_evidence's note of the sources it could not read."""
    named = " und ".join(_SOURCES[s] for s in found.group(1).lower().split(" and "))
    return f"{named[0].upper()}{named[1:]} konnten nicht gelesen werden."


def _why(german: Callable[[str, str, list[str]], str]) -> Callable[[re.Match[str]], str]:
    """A "Why:" line's German: `german` given the noun, its plural and the shape's numbers."""

    def say(found: re.Match[str]) -> str:
        noun = next(g for g in found.groups() if g in _NOUNS)
        numbers = [g for g in found.groups() if g != noun]
        return f"Begründung: {german(*_NOUNS[noun], numbers)}."

    return say


_GERMAN: list[tuple[re.Pattern[str], Say]] = [
    # progress_words and _next_words
    (
        re.compile(r"You have not assessed any of the (\d+) controls yet\."),
        r"Sie haben noch keine der \1 Kontrollen bewertet.",
    ),
    (
        re.compile(rf"You last worked on (\d+) ({'|'.join(_MONTHS)}) (\d{{4}})\."),
        lambda f: f"Sie haben zuletzt am {f[1]}. {_MONTHS[f[2]]} {f[3]} gearbeitet.",
    ),
    (re.compile(r"Of the (\d+) controls, ([^.]+)\."), _counts),
    (
        re.compile(rf"The next step is to review control {_ID} on its working paper\."),
        r"Der nächste Schritt ist, Kontrolle \1 auf ihrem Arbeitspapier zu prüfen.",
    ),
    (
        re.compile(rf"The next step is to assess control {_ID} again\."),
        r"Der nächste Schritt ist, Kontrolle \1 erneut zu bewerten.",
    ),
    (
        re.compile(rf"The next step is to assess control {_ID}\."),
        r"Der nächste Schritt ist, Kontrolle \1 zu bewerten.",
    ),
    (re.compile(r"Every control is accepted\."), "Alle Kontrollen sind akzeptiert."),
    (
        re.compile(r"No control applies under this profile, so there is nothing to assess\."),
        "Unter diesem Profil ist keine Kontrolle anwendbar, es gibt also nichts zu bewerten.",
    ),
    # A control not applicable as a whole (#454): refused, or explained with the reason
    (
        re.compile(rf"{_ID} is not applicable in this profile\."),
        r"\1 ist in diesem Profil nicht anwendbar.",
    ),
    (re.compile(r"The profile's reason: "), "Die Begründung des Profils: "),
    # assessed_words
    (
        _line(rf"Control {_ID}, {_QUOTED}: its newest assessment is (.+?), {_STATUS}\."),
        lambda f: (
            f"Die neueste Bewertung von Kontrolle {f[1]}, {f[2]}, lautet {f[3]}; "
            f"sie ist {_STATUSES[f[4]][0]}."
        ),
    ),
    (
        _line(rf"Why: Evidenced: its one {_NOUN} is fully evidenced; {_SHOULDS}\."),
        _why(
            lambda noun, _, n: (
                f"Nachgewiesen: die eine {noun} ist vollständig nachgewiesen; {_SOLL}"
            )
        ),
    ),
    (
        _line(rf"Why: Evidenced: all (\d+) {_NOUN}s are fully evidenced; {_SHOULDS}\."),
        _why(
            lambda _, nouns, n: (
                f"Nachgewiesen: alle {n[0]} {nouns} sind vollständig nachgewiesen; {_SOLL}"
            )
        ),
    ),
    (
        _line(rf"Why: Partly evidenced: (\d+) of (\d+) {_NOUN}s (?:is|are) fully evidenced\."),
        _why(
            lambda _, nouns, n: (
                f"Teilweise nachgewiesen: {n[0]} von {n[1]} {nouns} "
                f"{'ist' if n[0] == '1' else 'sind'} vollständig nachgewiesen"
            )
        ),
    ),
    (
        _line(
            rf"Why: Not evidenced: no {_NOUN} is fully evidenced "
            r"\((\d+) of (\d+) partly evidenced\)\."
        ),
        _why(
            lambda noun, _, n: (
                f"Nicht nachgewiesen: keine {noun} ist vollständig nachgewiesen "
                f"({n[0]} von {n[1]} teilweise nachgewiesen)"
            )
        ),
    ),
    (
        _line(rf"Why: Not evidenced: no {_NOUN} is evidenced; {_SHOULDS}\."),
        _why(lambda noun, _, n: f"Nicht nachgewiesen: keine {noun} ist nachgewiesen; {_SOLL}"),
    ),
    (
        _line(rf"Why: Not applicable: no {_NOUN} applies; {_SHOULDS}\."),
        _why(lambda noun, _, n: f"Nicht anwendbar: keine {noun} ist anwendbar; {_SOLL}"),
    ),
    (_line(r"Not yet evidenced: (.*)"), r"Noch nicht nachgewiesen: \1"),
    (_line(r"Evidenced: (.*)"), r"Nachgewiesen: \1"),
    (
        _line(r"Every requirement that applies is evidenced\."),
        "Jede Anforderung, die gilt, ist nachgewiesen.",
    ),
    (
        _line(r"The quotes these verdicts rest on, checked against each document:"),
        "Die Zitate, auf denen diese Bewertungen beruhen, je mit ihrem Dokument abgeglichen:",
    ),
    # not_assessed_words
    (
        _line(
            rf"Control {_ID}, {_QUOTED}, has not been assessed yet, so no evidence has been read "
            r"for it\. Its requirements under this profile:"
        ),
        (
            r"Kontrolle \1, \2, ist noch nicht bewertet, daher wurden für sie noch keine "
            r"Nachweise gelesen. Ihre Anforderungen unter diesem Profil:"
        ),
    ),
    # sign_off_words
    (
        re.compile(
            r"I cannot (accept|reject|override) a verdict: signing off is yours, and Vouch never "
            r"writes or changes a verdict for you\."
        ),
        lambda f: (
            f"Ich kann keine Bewertung {_SIGN_OFFS[f[1]]}: Die Entscheidung liegt bei Ihnen, und "
            "Vouch schreibt oder ändert nie eine Bewertung für Sie."
        ),
    ),
    (
        re.compile(
            rf"Control {_ID} has not been assessed yet, so there is no verdict to sign off\. "
            r"Its working paper is linked below\."
        ),
        (
            r"Kontrolle \1 ist noch nicht bewertet, daher gibt es keine Bewertung, über die Sie "
            r"entscheiden könnten. Ihr Arbeitspapier ist unten verlinkt."
        ),
    ),
    (
        re.compile(
            rf"You can override a requirement of control {_ID}, with your reason, at the foot of "
            r"its working paper, linked below\."
        ),
        (
            r"Sie können eine Anforderung der Kontrolle \1 mit Ihrer Begründung am Ende ihres "
            r"Arbeitspapiers überschreiben, das unten verlinkt ist."
        ),
    ),
    (
        re.compile(
            rf"You can accept or reject control {_ID}, with a note, at the foot of its working "
            r"paper, linked below\."
        ),
        (
            r"Sie können die Kontrolle \1 mit einer Notiz am Ende ihres Arbeitspapiers "
            r"akzeptieren oder ablehnen, das unten verlinkt ist."
        ),
    ),
    # assessed_controls_words
    (
        _line(r"Each control's newest assessment under this profile:"),
        "Die neueste Bewertung jeder Kontrolle unter diesem Profil:",
    ),
    (_line(rf"- {_ID}: not assessed yet\."), r"- \1: noch nicht bewertet."),
    (
        re.compile(rf"^(- {_ID}: .+?), {_STATUS}; its counted requirements rest ", re.MULTILINE),
        lambda f: f"{f[1]}, {_STATUSES[f[3]][0]}; ",
    ),
    (re.compile(rf"(?<=; )(only )?on (?:no cited|({_KIND_LIST})) evidence\."), _rests_on),
    (
        # A requirement in brief: "M1 (must) Nachgewiesen, documented, stale", up to "; " or ".".
        re.compile(
            rf"(\b[A-Z]+\d+ \(\w+\) [^;\n]+?)(?:, ({_KIND_LIST}))?(, stale)?(?=; |\.$)",
            re.MULTILINE,
        ),
        _requirement,
    ),
    (
        _line(f"({'|'.join(_LABELS)}): (.*)"),
        lambda f: f"{_LABELS[f[1]]}: {'keine.' if f[2] == 'none.' else f[2]}",
    ),
    # unknown_control
    (
        re.compile(rf"There is no VDA ISA control {_QUOTED}\. The controls are "),
        r"Die Kontrolle \1 gibt es in der VDA ISA nicht. Die Kontrollen sind ",
    ),
    # start_assessment
    (
        re.compile(
            rf"The assessment of control {_ID} has finished: (.+?)\. The verdict is proposed and "
            r"waits for your review on the control's working paper\."
        ),
        (
            r"Die Bewertung der Kontrolle \1 ist abgeschlossen: \2. Das Ergebnis ist "
            r"vorgeschlagen und wartet auf dem Arbeitspapier der Kontrolle auf Ihre Prüfung."
        ),
    ),
    # search_evidence
    (
        re.compile(rf"Found 1 passage for {_QUOTED}\."),
        r"1 Textstelle für \1 wurde gefunden, die hier mit ihrem Dokument zitiert wird.",
    ),
    (
        re.compile(rf"Found (\d+) passages for {_QUOTED}\."),
        r"\1 Textstellen für \2 wurden gefunden, die hier mit ihrem Dokument zitiert werden.",
    ),
    (re.compile(r"(The policies(?: and the records)?|The records) could not be read\."), _unread),
    (
        re.compile(re.escape(NO_EVIDENCE)),
        "Für diese Frage habe ich in Ihren Dokumenten keine Nachweise gefunden.",
    ),
    (
        re.compile(re.escape(NO_SEARCH_ALLOWED)),
        "Dieses Compliance-Profil erlaubt nicht, Richtlinien oder Aufzeichnungen zu durchsuchen.",
    ),
]
"""Each tool sentence's English shape (vouch.chat_agent's tools' words) and its German, formal and
with design.md's verdict words, applied in order (#330)."""


def german_answer(words: str) -> str:
    """The fake's answer to a tool's words (already cited) in German, sentence by sentence, for a
    profile that answers in German (#330): the tools write English for the model, as a real model
    answers in the profile's language. Cites, document ids, control and requirement ids and
    columns, the document's own texts, reasons, numbers and the quoted query stay as they are; a
    sentence the table doesn't know passes through unchanged. Document text is set aside first,
    so no rule reads it: each cite, and each line giving a requirement's text (a finding, with
    its reason, or a requirement not yet assessed)."""
    kept: list[str] = []

    def set_aside(found: re.Match[str]) -> str:
        kept.append(found.group(0))
        return f"\0{len(kept) - 1}\0"

    words = _DOCUMENT_TEXT.sub(set_aside, words)
    for shape, german in _GERMAN:
        words = shape.sub(german, words)
    return _SET_ASIDE.sub(lambda found: kept[int(found.group(1))], words)


_DOCUMENT_TEXT = re.compile(
    rf"{CITE.pattern}|^- [\w.]+ \(\w+\): [^\n]*$|\0", re.MULTILINE | re.DOTALL
)
"""What german_answer leaves as it is: a cite, a line with a requirement's text, or a NUL already
in the text, so the only NULs left are its own set-aside markers."""
_SET_ASIDE = re.compile(r"\0(\d+)\0")


def _page(messages: list[BaseMessage]) -> str | None:
    """The control of the page the lead is on, as the system prompt names it."""
    prompt = next((m for m in messages if isinstance(m, SystemMessage)), None)
    found = PAGE_CONTROL.search(str(prompt.content)) if prompt else None
    return found.group(1) if found else None


def _turns_tool_result(messages: list[BaseMessage]) -> ToolMessage | None:
    """The last tool result since the lead's last message (not the output check's), if any."""
    lead = max((i for i, m in enumerate(messages) if is_lead(m)), default=-1)
    results = [m for m in messages[lead + 1 :] if isinstance(m, ToolMessage)]
    return results[-1] if results else None


class CannedChatModel(BaseChatModel):
    """Answers every message with `reply`, streamed a word at a time (or as `chunks`, when
    given), with token counts estimated as the scripted fake's are, so the usage meter sees
    them. Each call's messages are kept in `prompts`; with `fail_with`, every call raises it,
    as a model that can't be reached would. With tools bound, it calls the one the lead's
    message asks for (or `call`, whatever is asked), writing `before_tool` with the call, and
    answers a tool's result with the result's words (or `after_tool`). With `blocks`, it writes
    its answer in content blocks, as some providers do. Asked again by the output check, it gives
    `retry_reply`, or else answers as it did before: the turn's last tool result passed on, else
    its reply in the profile's language (#208)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    reply: str = CHAT_REPLY
    chunks: list[str] | None = None
    fail_with: Exception | None = None
    prompts: list[list[BaseMessage]] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)  # the names of the tools bound
    call: dict[str, Any] | None = None  # {"name", "args"}: the tool call it makes
    before_tool: str = ""
    after_tool: str | None = None
    blocks: bool = False
    retry_reply: str | None = None  # its answer when asked again by the output check (#249)
    language: str = "en"  # the profile's answer language: its canned reply is in it (#233)

    @property
    def _llm_type(self) -> str:
        return "canned-chat-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "CannedChatModel":
        """The same fake (its `prompts` shared), now able to call these tools."""
        names = [tool["function"]["name"] for tool in tools]
        return self.model_copy(update={"tools": names})

    def _record_prompt_then_answer(
        self, messages: list[BaseMessage]
    ) -> tuple[list[str], list[ToolCall]]:
        """Keeps the call's messages, then raises `fail_with` if set, else gives the answer's
        pieces in order, or the tool call the lead's last message asks for."""
        self.prompts.append(list(messages))
        if self.fail_with is not None:
            raise self.fail_with
        last = messages[-1] if messages else None
        if last is not None and is_refusal(last):
            if self.retry_reply is not None:
                return _words(self.retry_reply), []
            # As it answered before (#208): the turn's last tool result passed on, else its reply.
            result = _turns_tool_result(messages)
            return _words(self._after(result) if result else self._reply()), []
        if isinstance(last, ToolMessage):
            return _words(self._after(last)), []
        call = self._tool_call(last, messages) if isinstance(last, HumanMessage) else None
        if call is not None:
            return _words(self.before_tool), [call]
        return (self.chunks if self.chunks is not None else _words(self._reply())), []

    def _after(self, result: ToolMessage) -> str:
        """Its answer to a tool's result: `after_tool`, else the result's words, cited, and in
        German on a German profile (#330)."""
        if self.after_tool:
            return self.after_tool
        cited = _cited(str(result.content))
        return german_answer(cited) if self.language == "de" else cited

    def _reply(self) -> str:
        """`reply`, and the canned one in the profile's language where the fake has it (#233)."""
        return (
            CHAT_REPLIES.get(self.language, self.reply) if self.reply == CHAT_REPLY else self.reply
        )

    def _tool_call(self, message: HumanMessage, messages: list[BaseMessage]) -> ToolCall | None:
        text = str(message.content)
        call_id = f"call-{len(self.prompts)}"

        def call(name: str, **args: str) -> ToolCall | None:
            return ToolCall(name=name, args=args, id=call_id) if name in self.tools else None

        if self.call is not None:
            return ToolCall(name=self.call["name"], args=self.call["args"], id=call_id)
        if asked := SIGN_OFF.search(text):
            verb = asked.group(1).lower()
            sign_off = verb if verb in ("reject", "override") else "accept"
            return call(EXPLAIN_CONTROL, control_id=asked.group(2), sign_off=sign_off)
        if asked := ASSESS.search(text):
            return call(START_ASSESSMENT, control_id=asked.group(1).rstrip(".?!,;:"))
        if asked := SINCE_ROUND.search(text):
            named = asked.group(1).strip()
            if LAST_ROUND.match(named):
                return call(COMPARE_ROUNDS)
            return call(COMPARE_ROUNDS, round=named)
        if ACROSS_CONTROLS.search(text):
            return call(ASSESSED_CONTROLS)
        if text == GREETING_REQUEST or WHERE.search(text):
            return call(PROGRESS)
        if (asked := MISSING.search(text)) and (control_id := asked.group(1) or _page(messages)):
            return call(EXPLAIN_CONTROL, control_id=control_id)
        if asked := EVIDENCE.search(text):
            return call(SEARCH_EVIDENCE, query=asked.group(1))
        return None

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        pieces, calls = self._record_prompt_then_answer(messages)
        text = "".join(pieces)
        reply = AIMessage(text, tool_calls=calls, usage_metadata=_usage(messages, text))
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        pieces, calls = self._record_prompt_then_answer(messages)
        empty: str | list[Any] = [] if self.blocks else ""
        for piece in pieces:
            content = [{"type": "text", "text": piece, "index": 0}] if self.blocks else piece
            yield ChatGenerationChunk(message=AIMessageChunk(content=content))
        for index, call in enumerate(calls):
            chunk = {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"]}
            message = AIMessageChunk(content=empty, tool_call_chunks=[chunk | {"index": index}])
            yield ChatGenerationChunk(message=message)
        # The whole call's token counts, on a last empty chunk, as OpenAI-style streams give them.
        usage = _usage(messages, "".join(pieces))
        yield ChatGenerationChunk(message=AIMessageChunk(content=empty, usage_metadata=usage))
