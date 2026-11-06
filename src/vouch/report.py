"""The readiness report (#234): where one compliance profile stands, for management or an
auditor, in the profile's answer language. No model is called: it is built from the data the
Dossier already reads.

- the head: the profile's company, country, base framework, protection level, country overlays
  and answer language, and the date it was drawn up;
- readiness, as the Dossier overview counts it (vouch.dossier.readiness), so the two never
  disagree: accepted verdicts only (GLOSSARY: Readiness); but its `remediation_items` counts the
  approved items the report lists, where the overview's counts every item not discarded;
- each control in the base framework's order: an accepted one with its reviewed verdict, its
  reviewer, the review's time and its quotes; any other is open;
- the approved remediation items of each accepted control's newest assessment, under it;
- the requirements the profile marks not applicable, with its reasons;
- a page of the audit trail, newest first (vouch.audit.read);
- the disclaimer, naming the reviewers of the accepted verdicts.

The report's own words (headings, column names, verdict and action words, the sentences for an
open control or an empty list, the disclaimer) come from one table per language, WORDS, sent with
the data (`words_for`) so the page and the Markdown export say the same thing. A language without a table gets
English. None of them claims an audit result (vouch.language.audit_claim); text carried from
elsewhere (quotes, the lead's notes, the model's remediation text) stays as written.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any, Literal, TypedDict, TypeGuard

from vouch import audit
from vouch.citations import EvidenceKind
from vouch.dossier import Readiness, control_requirements, readiness
from vouch.graph import Assessment, Verdict
from vouch.isa import IsaControl
from vouch.profile import ComplianceProfile, ProtectionLevel
from vouch.remediation import DecidedItem, Key

ControlStatus = Literal["accepted", "open"]
DEFAULT_LANGUAGE = "en"

WORDS: dict[str, dict[str, Any]] = {
    "en": {
        "title": "Readiness report",
        "labels": {
            "company": "Company",
            "country": "Country",
            "base_framework": "Base framework",
            "protection_level": "Protection level",
            "overlays": "Country overlays",
            "answer_language": "Language",
            "date": "Drawn up on",
            "readiness": "Readiness",
            "controls": "Controls",
            "control": "Control",
            "question": "Question",
            "status": "Status",
            "verdict": "Verdict",
            "reviewer": "Reviewed by",
            "reviewed_at": "Reviewed on",
            "quotes": "Quotes",
            "document": "Document",
            "version": "Version",
            "section": "Section",
            # Stale evidence (GLOSSARY.md): the document was past its review cycle when checked.
            "stale": "Stale",
            "remediation": "Remediation items",
            "requirement": "Requirement",
            "missing": "Missing",
            "owner_role": "Owner role",
            "due_date": "Due date",
            "not_applicable": "Not applicable",
            "reason": "Reason",
            "trail": "Audit trail",
            "at": "When",
            "who": "Who",
            "action": "Action",
            "note": "Note",
            "verdict_before": "Verdict before",
            "verdict_after": "Verdict after",
            "evidence": "Evidence",
            "model": "Model",
            "prompt_version": "Prompt version",
        },
        "verdicts": {
            "evidenced": "Evidenced",
            "partly-evidenced": "Partly evidenced",
            "not-evidenced": "Not evidenced",
            "not-applicable": "Not applicable",
        },
        "statuses": {"accepted": "Accepted", "open": "Open"},
        "actions": {
            "accept": "Accepted",
            "reject": "Rejected",
            "override": "Overridden",
            "approve": "Approved",
            "discard": "Discarded",
        },
        "protection_levels": {"normal": "Normal", "high": "High", "very high": "Very high"},
        "languages": {"en": "English", "de": "German"},
        "sentences": {
            "accepted_of": "{accepted} of {controls} controls accepted",
            "no_overlays": "No country overlay applies to this profile.",
            "open_control": "Open: not yet accepted, so it has no reviewed verdict.",
            "no_remediation": "No remediation item of an accepted control is approved yet.",
            "no_not_applicable": "The profile marks no requirement as not applicable.",
            "no_trail": "The audit trail is empty: no review has been recorded yet.",
            "older_entries": "Older entries are in the audit trail.",
            "disclaimer": "Drafted by an AI assistant, reviewed by {names}; not an audit result.",
            "disclaimer_nothing_reviewed": (
                "Drafted by an AI assistant; nothing has been reviewed yet; not an audit result."
            ),
            "and": "and",  # joins the last two names
        },
    },
    "de": {
        "title": "Bereitschaftsbericht",
        "labels": {
            "company": "Unternehmen",
            "country": "Land",
            "base_framework": "Basis-Regelwerk",
            "protection_level": "Schutzbedarf",
            "overlays": "Länderergänzungen",
            "answer_language": "Sprache",
            "date": "Erstellt am",
            "readiness": "Bereitschaft",
            "controls": "Kontrollen",
            "control": "Kontrolle",
            "question": "Frage",
            "status": "Status",
            "verdict": "Bewertung",
            "reviewer": "Geprüft von",
            "reviewed_at": "Geprüft am",
            "quotes": "Zitate",
            "document": "Dokument",
            "version": "Version",
            "section": "Abschnitt",
            "stale": "Prüfung überfällig",
            "remediation": "Maßnahmen",
            "requirement": "Anforderung",
            "missing": "Fehlt",
            "owner_role": "Verantwortliche Rolle",
            "due_date": "Fällig am",
            "not_applicable": "Nicht anwendbar",
            "reason": "Begründung",
            "trail": "Prüfpfad",
            "at": "Wann",
            "who": "Wer",
            "action": "Aktion",
            "note": "Notiz",
            "verdict_before": "Bewertung vorher",
            "verdict_after": "Bewertung nachher",
            "evidence": "Nachweise",
            "model": "Modell",
            "prompt_version": "Prompt-Version",
        },
        "verdicts": {
            "evidenced": "Nachgewiesen",
            "partly-evidenced": "Teilweise nachgewiesen",
            "not-evidenced": "Nicht nachgewiesen",
            "not-applicable": "Nicht anwendbar",
        },
        "statuses": {"accepted": "Akzeptiert", "open": "Offen"},
        "actions": {
            "accept": "Akzeptiert",
            "reject": "Abgelehnt",
            "override": "Überschrieben",
            "approve": "Freigegeben",
            "discard": "Verworfen",
        },
        "protection_levels": {"normal": "Normal", "high": "Hoch", "very high": "Sehr hoch"},
        "languages": {"en": "Englisch", "de": "Deutsch"},
        "sentences": {
            "accepted_of": "{accepted} von {controls} Kontrollen akzeptiert",
            "no_overlays": "Für dieses Profil gilt keine Länderergänzung.",
            "open_control": "Offen: noch nicht akzeptiert, daher ohne geprüfte Bewertung.",
            "no_remediation": "Für die akzeptierten Kontrollen ist noch keine Maßnahme freigegeben.",
            "no_not_applicable": "Das Profil markiert keine Anforderung als nicht anwendbar.",
            "no_trail": "Der Prüfpfad ist leer: noch wurde keine Prüfung erfasst.",
            "older_entries": "Ältere Einträge stehen im Prüfpfad.",
            "disclaimer": (
                "Erstellt von einem KI-Assistenten, geprüft von {names}; kein Auditergebnis."
            ),
            "disclaimer_nothing_reviewed": (
                "Erstellt von einem KI-Assistenten; noch nichts geprüft; kein Auditergebnis."
            ),
            "and": "und",
        },
    },
}


class Head(TypedDict):
    company: str
    country: str
    base_framework: str
    protection_level: ProtectionLevel
    overlays: list[str]  # the country overlays' names
    answer_language: str  # ISO 639-1
    words_language: str  # ISO 639-1: the language of the report's words (English without a table)
    date: str  # ISO 8601: the day the report was drawn up


class ReportQuote(TypedDict):
    doc_id: str
    version: str
    section: str
    quote: str
    kind: EvidenceKind
    stale: bool  # stale on the day it was checked: the page marks it for the reader


class ReportControl(TypedDict):
    id: str
    question: str
    status: ControlStatus
    verdict: Verdict | None  # the reviewed verdict; None while open
    reviewer: str | None  # the reviewer's display name, else their id, else None
    reviewed_at: str | None  # ISO 8601
    quotes: list[ReportQuote]


class ReportItem(TypedDict):
    requirement_id: str
    missing: str
    owner_role: str
    due_date: str  # ISO 8601


class ControlItems(TypedDict):
    control_id: str
    question: str
    items: list[ReportItem]


class NotApplicableEntry(TypedDict):
    control_id: str
    requirement_id: str
    text: str
    reason: str


class Trail(TypedDict):
    entries: list[dict[str, Any]]  # vouch.audit.AuditEntry.to_dict, newest first
    next: int | None  # GET /api/audit-trail's `before` for the older entries; None: no more


class Report(TypedDict):
    head: Head
    readiness: Readiness
    controls: list[ReportControl]
    remediation: list[ControlItems]
    not_applicable: list[NotApplicableEntry]
    trail: Trail
    disclaimer: str


def today() -> date:
    """The day a report is drawn up: the server's date in UTC, as the trail's times are."""
    return datetime.now(UTC).date()


def words_language(language: str) -> str:
    """The language the report's own words are in for an answer language (ISO 639-1): itself
    when it has a table, else English. The page marks the report's `lang` with it."""
    return language if language in WORDS else DEFAULT_LANGUAGE


def words_for(language: str) -> dict[str, Any]:
    """The report's own words in the language (ISO 639-1); English when it has no table."""
    return WORDS[words_language(language)]


def disclaimer(names: Iterable[str], language: str) -> str:
    """The AI disclaimer, naming the reviewers in the order given (each once), joined in the
    language ("A, B and C", "A, B und C"); the nothing-reviewed wording when there are none."""
    sentences = words_for(language)["sentences"]
    unique = list(dict.fromkeys(names))
    if not unique:
        return sentences["disclaimer_nothing_reviewed"]
    *first, last = unique
    joined = f"{', '.join(first)} {sentences['and']} {last}" if first else last
    return sentences["disclaimer"].format(names=joined)


def readiness_report(
    profile: ComplianceProfile,
    controls: Iterable[IsaControl],
    latest: Mapping[str, Assessment],
    *,
    decided: Mapping[Key, DecidedItem],
    trail: audit.Page,
    today: date,
) -> Report:
    """The report for the profile's `controls` (its ISA edition's, in order), from each
    control's newest assessment (`latest`, vouch.memory.newest_by_control), the lead's decided
    remediation items of the accepted ones (`decided`), and a page of the trail. Its words,
    the same for every report in the language, are `words_for(profile.answer_language)`."""
    controls = list(controls)
    shown = [_control(control, latest.get(control.id)) for control in controls]
    reviewers = [row["reviewer"] for row in shown if row["reviewer"] is not None]
    remediation = _remediation(controls, latest, decided)
    approved = sum(len(group["items"]) for group in remediation)
    return {
        "head": {
            "company": profile.company,
            "country": profile.country,
            "base_framework": profile.base_framework,
            "protection_level": profile.protection_level,
            "overlays": [overlay.name for overlay in profile.overlays],
            "answer_language": profile.answer_language,
            "words_language": words_language(profile.answer_language),
            "date": today.isoformat(),
        },
        # The accepted counts as the overview's; the items as the report lists them.
        "readiness": readiness(controls, latest) | {"remediation_items": approved},
        "controls": shown,
        "remediation": remediation,
        "not_applicable": _not_applicable(profile, controls),
        "trail": {"entries": [e.to_dict() for e in trail.entries], "next": trail.next},
        "disclaimer": disclaimer(reviewers, profile.answer_language),
    }


def _accepted(assessment: Assessment | None) -> TypeGuard[Assessment]:
    return assessment is not None and assessment.status == "accepted"


def _control(control: IsaControl, assessment: Assessment | None) -> ReportControl:
    if not _accepted(assessment):
        return {
            "id": control.id,
            "question": control.question,
            "status": "open",
            "verdict": None,
            "reviewer": None,
            "reviewed_at": None,
            "quotes": [],
        }
    review = assessment.review
    reviewer = (review.reviewer_name or review.reviewer_id or None) if review else None
    return {
        "id": control.id,
        "question": control.question,
        "status": "accepted",
        "verdict": assessment.verdict,
        "reviewer": reviewer,
        "reviewed_at": (review.reviewed_at or None) if review else None,
        "quotes": _quotes(assessment),
    }


def _quotes(assessment: Assessment) -> list[ReportQuote]:
    """Each quote the assessment cites, once, in its requirements' order."""
    quotes: dict[tuple[str, str, str], ReportQuote] = {}
    for requirement in assessment.requirements:
        for citation in requirement.citations:
            quotes.setdefault(
                (citation.doc_id, citation.version, citation.quote),
                {
                    "doc_id": citation.doc_id,
                    "version": citation.version,
                    "section": citation.section,
                    "quote": citation.quote,
                    "kind": citation.kind,
                    "stale": citation.stale,
                },
            )
    return list(quotes.values())


def _remediation(
    controls: list[IsaControl],
    latest: Mapping[str, Assessment],
    decided: Mapping[Key, DecidedItem],
) -> list[ControlItems]:
    """The approved items of each accepted control's newest assessment, in its items' order,
    under their control; a control with none is left out."""
    grouped: list[ControlItems] = []
    for control in controls:
        assessment = latest.get(control.id)
        if not _accepted(assessment):
            continue
        items: list[ReportItem] = []
        for draft in assessment.remediation:
            kept = decided.get((assessment.thread_id, draft.requirement_id))
            if kept is not None and kept.status == "approved":
                items.append(
                    {
                        "requirement_id": kept.requirement_id,
                        "missing": kept.missing,
                        "owner_role": kept.owner_role,
                        "due_date": kept.due_date.isoformat(),
                    }
                )
        if items:
            grouped.append({"control_id": control.id, "question": control.question, "items": items})
    return grouped


def _not_applicable(
    profile: ComplianceProfile, controls: list[IsaControl]
) -> list[NotApplicableEntry]:
    return [
        {
            "control_id": control.id,
            "requirement_id": r["id"],
            "text": r["text"],
            "reason": r["not_applicable"],
        }
        for control in controls
        for r in control_requirements(profile, control)
        if r["not_applicable"] is not None
    ]
