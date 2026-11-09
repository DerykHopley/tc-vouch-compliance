"""Open questions on a control (#414, spec #350 stories 11 and 12), in both goals.

The lead logs a question they can't answer alone on any control of the profile, assessed or not:
what is asked, who it is for (`asked_of`) and when it is due; and records its answer once it
comes. An answer is final: the same answer again writes nothing, another is refused naming the
kept one. Nothing edits or deletes a question: lead-supplied evidence (#422, `lead_statements`)
cites an answer by the question's id.

The questions are kept per user and profile, each write with its audit entry (`question`,
`answer`): in Postgres, table vouch_open_questions, in one transaction, an answer under its
question's row lock (vouch.database's `log_question`, `answer_question`); without a database,
this process's memory and trail, whose writes can't fail. A fresh start hides the questions asked
before it (`since`), as it hides assessments; they stay in the table and the trail.

A question is overdue while it has no answer and its due date is before today in UTC
(`is_overdue`). A re-assessment of the control is suggested (`reasons`, beside the improvement
plan's `fixes-done`) while its newest assessment is accepted, and a question on it was answered
after that acceptance (`question-answered`), or one with no answer is overdue and that assessment
was accepted before it fell overdue (`question-overdue`). A control not assessed, waiting for
review or rejected has neither: its next step is to assess or review it anyway.
"""

import itertools
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, StringConstraints

from vouch.audit import AuditEntry, ProcessTrail, Reviewer, now, process_trail
from vouch.citations import LeadStatement
from vouch.database import AuditAction, Database
from vouch.graph import NO_NUL_PATTERN, Assessment
from vouch.plan import REASSESS_REASONS, ReassessReason, accepted_at
from vouch.remediation import MAX_OWNER_ROLE, check_due

MAX_QUESTION = 1000
"""A question, trimmed, is 1 to MAX_QUESTION characters."""
MAX_ANSWER = 2000
"""An answer, trimmed, is 1 to MAX_ANSWER characters."""

NO_QUESTION = "There is no such open question on control {control_id}."
ANSWERED_WITH = "This question was answered already: {answer}"


class UnknownQuestionError(LookupError):
    """No such question of the user's under the profile, on that control (a 404)."""


class AnsweredAlreadyError(RuntimeError):
    """The question was answered already, with another answer (a 409)."""


class QuestionRequest(BaseModel):
    """The question the lead logs on a control: what, for whom, due when."""

    question: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_QUESTION, pattern=NO_NUL_PATTERN
        ),
    ]
    asked_of: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_OWNER_ROLE, pattern=NO_NUL_PATTERN
        ),
    ]
    due_date: date


class AnswerRequest(BaseModel):
    """The answer the lead records to a question."""

    answer: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_ANSWER, pattern=NO_NUL_PATTERN
        ),
    ]


@dataclass(frozen=True)
class OpenQuestion:
    """A question on a control, as kept; `answer` and `answered_at` None until answered. `id` is
    0 until the question is kept."""

    id: int
    control_id: str
    question: str
    asked_of: str
    due_date: date
    asked_at: datetime
    answer: str | None = None
    answered_at: datetime | None = None


def overdue_from(question: OpenQuestion) -> datetime:
    """When the question falls overdue: the start, in UTC, of the day after its due date."""
    return datetime.combine(question.due_date + timedelta(days=1), time.min, UTC)


def is_overdue(question: OpenQuestion, at: datetime) -> bool:
    """Not answered, and its due date is before `at`'s day in UTC."""
    return question.answer is None and at >= overdue_from(question)


def shown(question: OpenQuestion, at: datetime) -> dict[str, Any]:
    """The question as the API sends it (not yet redacted), `overdue` as of `at`."""
    answered = question.answered_at
    return {
        "id": question.id,
        "question": question.question,
        "asked_of": question.asked_of,
        "due_date": question.due_date.isoformat(),
        "asked_at": question.asked_at.isoformat(),
        "answer": question.answer,
        "answered_at": None if answered is None else answered.isoformat(),
        "overdue": is_overdue(question, at),
    }


def since(questions: Iterable[OpenQuestion], fresh_start: datetime | None) -> list[OpenQuestion]:
    """The questions asked at or after the fresh start (all of them when there was none)."""
    return [q for q in questions if fresh_start is None or q.asked_at >= fresh_start]


def lead_statements(questions: Iterable[OpenQuestion]) -> list[LeadStatement]:
    """The answered ones of the questions, as the lead's statements on their controls (#422):
    lead-supplied evidence, dated by the day each was answered in UTC."""
    return [
        LeadStatement(q.id, q.control_id, q.answer, q.answered_at.astimezone(UTC).date())
        for q in questions
        if q.answer is not None and q.answered_at is not None
    ]


def by_control(questions: Iterable[OpenQuestion], at: datetime) -> dict[str, list[dict[str, Any]]]:
    """The questions as the overview sends them, by control id, in the order given (oldest
    first, as the stores list them); only controls with a question are named."""
    listed: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        listed.setdefault(question.control_id, []).append(shown(question, at))
    return listed


def reasons(
    latest: Mapping[str, Assessment], questions: Iterable[OpenQuestion], at: datetime
) -> dict[str, list[ReassessReason]]:
    """The question reasons each control's re-assessment is suggested for, as of `at`, from its
    newest assessment (`latest`, vouch.memory's `newest_by_control`): only while that one is
    accepted. `question-answered` when a question was answered after it was accepted;
    `question-overdue` when a question with no answer is overdue and it was accepted before the
    question fell overdue. Each once, in REASSESS_REASONS' order; only controls with a reason
    are named."""
    found: dict[str, set[ReassessReason]] = {}
    for question in questions:
        assessment = latest.get(question.control_id)
        accepted = accepted_at(assessment) if assessment is not None else None
        if accepted is None:
            continue
        mine = found.setdefault(question.control_id, set())
        if question.answered_at is not None and question.answered_at > accepted:
            mine.add("question-answered")
        elif is_overdue(question, at) and accepted < overdue_from(question):
            mine.add("question-overdue")
    return {
        control_id: [r for r in REASSESS_REASONS if r in mine]
        for control_id, mine in found.items()
        if mine
    }


class OpenQuestions(Protocol):
    """Where each user's open questions under each profile are kept, each write with its audit
    entry."""

    def listed(self, user_id: str, profile_id: str) -> list[OpenQuestion]:
        """The user's questions under the profile, oldest first."""
        ...

    def log(
        self, user_id: str, profile_id: str, question: OpenQuestion, entry: AuditEntry
    ) -> OpenQuestion:
        """Keep `question` with `entry`, together: the question as kept, with its id."""
        ...

    def answer(
        self,
        user_id: str,
        profile_id: str,
        control_id: str,
        question_id: int,
        answer: str,
        at: datetime,
        entry: AuditEntry,
    ) -> tuple[OpenQuestion | None, bool]:
        """Record `answer` at `at` with `entry`, together, on the question with that id on the
        control (two at once take turns): (None, False) when there is none; (the question as
        kept, False) when it was answered already, writing nothing; else (it answered, True)."""
        ...


class ProcessQuestions:
    """Questions without a database: in this process, their entries in its audit trail."""

    def __init__(self, trail: Callable[[], ProcessTrail] = process_trail) -> None:
        self._trail = trail
        self._questions: dict[tuple[str, str], list[OpenQuestion]] = {}
        self._ids = itertools.count(1)
        self._guard = threading.Lock()

    def listed(self, user_id: str, profile_id: str) -> list[OpenQuestion]:
        with self._guard:
            return list(self._questions.get((user_id, profile_id), []))

    def log(
        self, user_id: str, profile_id: str, question: OpenQuestion, entry: AuditEntry
    ) -> OpenQuestion:
        with self._guard:
            kept = replace(question, id=next(self._ids))
            self._trail().keep([entry])
            self._questions.setdefault((user_id, profile_id), []).append(kept)
        return kept

    def answer(
        self,
        user_id: str,
        profile_id: str,
        control_id: str,
        question_id: int,
        answer: str,
        at: datetime,
        entry: AuditEntry,
    ) -> tuple[OpenQuestion | None, bool]:
        with self._guard:
            mine = self._questions.get((user_id, profile_id), [])
            index = next(
                (
                    i
                    for i, q in enumerate(mine)
                    if q.id == question_id and q.control_id == control_id
                ),
                None,
            )
            if index is None:
                return None, False
            if mine[index].answer is not None:
                return mine[index], False
            answered = replace(mine[index], answer=answer, answered_at=at)
            self._trail().keep([entry])
            mine[index] = answered
        return answered, True


def _question(row: Mapping[str, Any]) -> OpenQuestion:
    return OpenQuestion(
        id=row["id"],
        control_id=row["control_id"],
        question=row["question"],
        asked_of=row["asked_of"],
        due_date=row["due_date"],
        asked_at=row["asked_at"],
        answer=row["answer"],
        answered_at=row["answered_at"],
    )


def _row(entry: AuditEntry) -> dict[str, Any]:
    """An entry as a mapping of vouch.database's AUDIT_COLUMNS."""
    return {k: v for k, v in asdict(entry).items() if k != "id"}


class PostgresQuestions:
    """Questions in Postgres (vouch.database), each write in one transaction with its entry."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def listed(self, user_id: str, profile_id: str) -> list[OpenQuestion]:
        return [_question(r) for r in self.db.open_questions(user_id, profile_id)]

    def log(
        self, user_id: str, profile_id: str, question: OpenQuestion, entry: AuditEntry
    ) -> OpenQuestion:
        row = {k: v for k, v in asdict(question).items() if k != "id"}
        row |= {"user_id": user_id, "profile_id": profile_id}
        return _question(self.db.log_question(row, _row(entry)))

    def answer(
        self,
        user_id: str,
        profile_id: str,
        control_id: str,
        question_id: int,
        answer: str,
        at: datetime,
        entry: AuditEntry,
    ) -> tuple[OpenQuestion | None, bool]:
        key = {
            "id": question_id,
            "user_id": user_id,
            "profile_id": profile_id,
            "control_id": control_id,
        }
        kept, written = self.db.answer_question(key, answer, at, _row(entry))
        return (None if kept is None else _question(kept)), written


@cache
def process_questions() -> ProcessQuestions:
    """Questions without a database: one store per process, so a restart forgets them with the
    assessments."""
    return ProcessQuestions()


def _entry(
    reviewer: Reviewer,
    profile_id: str,
    control_id: str,
    action: AuditAction,
    note: str,
    at: datetime,
) -> AuditEntry:
    """The audit entry of a question logged or answered: who, when, under which profile, on
    which control, and the note; no thread (as a round's), requirement, verdict, evidence,
    model or prompt."""
    return AuditEntry(
        id=None,
        user_id=reviewer.id,
        reviewer_name=reviewer.name,
        at=at,
        profile_id=profile_id,
        control_id=control_id,
        thread_id="",
        action=action,
        requirement_id=None,
        note=note,
        verdict_before=None,
        verdict_after=None,
        evidence=(),
        model="",
        prompt_version="",
    )


def log(
    store: OpenQuestions,
    control_id: str,
    request: QuestionRequest,
    *,
    reviewer: Reviewer,
    profile_id: str,
    clock: Callable[[], datetime] = now,
) -> OpenQuestion:
    """The lead logs a question on the control (one of the profile's: the caller checks), with
    its `question` audit entry, together; the question as kept. Raises DueDateError for a due
    date past everywhere or beyond the horizon (vouch.remediation's `check_due`), and
    DatabaseUnavailable (Postgres)."""
    at = clock()
    check_due(request.due_date, at)
    question = OpenQuestion(
        id=0,
        control_id=control_id,
        question=request.question,
        asked_of=request.asked_of,
        due_date=request.due_date,
        asked_at=at,
    )
    note = f"{request.question} (for {request.asked_of}, due {request.due_date.isoformat()})"
    entry = _entry(reviewer, profile_id, control_id, "question", note, at)
    return store.log(reviewer.id, profile_id, question, entry)


def answer(
    store: OpenQuestions,
    control_id: str,
    question_id: int,
    text: str,
    *,
    reviewer: Reviewer,
    profile_id: str,
    clock: Callable[[], datetime] = now,
) -> OpenQuestion:
    """The lead records the answer (`text`, trimmed already) to their question on the control,
    with its `answer` audit entry, together; the question as kept. The same answer again writes
    nothing. Raises UnknownQuestionError (another user's, profile's or control's question too),
    AnsweredAlreadyError (answered with another answer) and DatabaseUnavailable (Postgres)."""
    at = clock()
    entry = _entry(reviewer, profile_id, control_id, "answer", text, at)
    kept, written = store.answer(reviewer.id, profile_id, control_id, question_id, text, at, entry)
    if kept is None:
        raise UnknownQuestionError(NO_QUESTION.format(control_id=control_id))
    if not written and kept.answer != text:
        raise AnsweredAlreadyError(ANSWERED_WITH.format(answer=kept.answer))
    return kept
