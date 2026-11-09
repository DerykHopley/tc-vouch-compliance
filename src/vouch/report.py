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

`markdown` writes a served report as one Markdown file (#57): the page's parts in the page's
order, in the report's words.

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
            "remediation_one": "Remediation item",
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
            "confirm": "Findings confirmed",
            "approve": "Approved",
            "discard": "Discarded",
            "goal": "Goal changed from {before} to {after}",
            "round-start": "Review round started: {round}",
            "round-close": "Review round closed: {round}",
        },
        "goals": {
            "audit-readiness": "Audit readiness",
            "internal-improvement": "Internal improvement",
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
            "page_of": "Page {page} of {pages}",  # the PDF's footer (#352)
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
            "remediation_one": "Maßnahme",
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
            "confirm": "Befunde bestätigt",
            "approve": "Freigegeben",
            "discard": "Verworfen",
            "goal": "Ziel geändert von {before} zu {after}",
            "round-start": "Prüfrunde begonnen: {round}",
            "round-close": "Prüfrunde abgeschlossen: {round}",
        },
        # As `actions.goal` reads them, after "von" and "zu" (dative): "Interner Verbesserung".
        "goals": {
            "audit-readiness": "Audit-Bereitschaft",
            "internal-improvement": "Interner Verbesserung",
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
            "page_of": "Seite {page} von {pages}",
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


COUNTRY_NAMES: dict[str, dict[str, str]] = {
    # A profile's country is stored in English (the top bar finds its ISO code from it); a report
    # in another language names it from here (#379), and one missing here shows as stored.
    "de": {
        "Austria": "Österreich",
        "Belgium": "Belgien",
        "Czechia": "Tschechien",
        "Denmark": "Dänemark",
        "France": "Frankreich",
        "Germany": "Deutschland",
        "Hungary": "Ungarn",
        "Italy": "Italien",
        "Liechtenstein": "Liechtenstein",
        "Luxembourg": "Luxemburg",
        "Netherlands": "Niederlande",
        "Poland": "Polen",
        "Slovakia": "Slowakei",
        "Slovenia": "Slowenien",
        "Spain": "Spanien",
        "Sweden": "Schweden",
        "Switzerland": "Schweiz",
        "United Kingdom": "Vereinigtes Königreich",
    },
}


def country_in(country: str, language: str) -> str:
    """The profile's country (stored in English) in the report's words for the language; as
    stored when they don't name it (#379)."""
    return COUNTRY_NAMES.get(words_language(language), {}).get(country, country)


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
            "country": country_in(profile.country, profile.answer_language),
            "base_framework": profile.base_framework,
            "protection_level": profile.protection_level,
            "overlays": [overlay.name for overlay in profile.overlays],
            "answer_language": profile.answer_language,
            "words_language": words_language(profile.answer_language),
            "date": today.isoformat(),
        },
        # The accepted counts as the overview's; the items as the report lists them.
        "readiness": readiness(controls, latest)
        | {"remediation_items": approved, "remediation_approved": approved},
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


# --- The downloads' rows (#57, #308) ----------------------------------------------------------
#
# What both downloads write, in the report's words, before either formats it: the Markdown
# export (below) and the PDF (vouch.report_pdf) only lay these out, so the two never disagree.
# Carried text comes back as written; each writer makes it safe for its own format.


def head_rows(head: Mapping[str, Any], words: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The head as (label, value) pairs, in the page's order."""
    labels = words["labels"]
    overlays = ", ".join(head["overlays"]) or words["sentences"]["no_overlays"]
    return [
        (labels["company"], head["company"]),
        (labels["country"], head["country"]),
        (labels["base_framework"], head["base_framework"]),
        (labels["protection_level"], words["protection_levels"][head["protection_level"]]),
        (labels["overlays"], overlays),
        # The words name English and German only: another answer language shows as its code.
        (
            labels["answer_language"],
            words["languages"].get(head["answer_language"], head["answer_language"]),
        ),
        (labels["date"], head["date"]),
    ]


def remediation_count(n: int, words: Mapping[str, Any]) -> str:
    """'1 Remediation item', '2 Remediation items': the count with its label, singular for one."""
    labels = words["labels"]
    return f"{n} {labels['remediation_one'] if n == 1 else labels['remediation']}"


def readiness_lines(r: Mapping[str, Any], words: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Readiness as its sentence and its counts in words, one per verdict, then open and items."""
    sentence = words["sentences"]["accepted_of"].format(
        accepted=r["accepted"], controls=r["controls"]
    )
    counts = [f"{n} {words['verdicts'][v]}" for v, n in r["by_verdict"].items()]
    counts += [
        f"{r['open']} {words['statuses']['open']}",
        remediation_count(r["remediation_items"], words),
    ]
    return sentence, counts


def day_of(iso: str) -> str:
    """An ISO time's day in UTC, as the page shows it; one that doesn't parse as written."""
    try:
        return datetime.fromisoformat(iso).astimezone(UTC).date().isoformat()
    except ValueError:
        return iso


def minute_of(iso: str) -> str:
    """An ISO time to the minute in UTC (`2026-11-06 14:05 UTC`), as the page shows it."""
    try:
        return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def control_columns(words: Mapping[str, Any]) -> list[str]:
    labels = words["labels"]
    return [
        labels[k] for k in ("control", "question", "status", "verdict", "reviewer", "reviewed_at")
    ]


def control_rows(controls: list[Mapping[str, Any]], words: Mapping[str, Any]) -> list[list[str]]:
    """The controls table's rows: an accepted control's verdict, reviewer and day; an open one's
    sentence in the verdict's place."""
    rows: list[list[str]] = []
    for c in controls:
        status = words["statuses"][c["status"]]
        if c["status"] == "accepted" and c["verdict"]:
            reviewed = day_of(c["reviewed_at"]) if c["reviewed_at"] else ""
            verdict = words["verdicts"][c["verdict"]]
            rows.append([c["id"], c["question"], status, verdict, c["reviewer"] or "", reviewed])
        else:
            open_control = words["sentences"]["open_control"]
            rows.append([c["id"], c["question"], status, open_control, "", ""])
    return rows


def quoted(controls: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The accepted controls with quotes: each gets its quotes under a heading of its own."""
    return [c for c in controls if c["status"] == "accepted" and c["quotes"]]


def quote_source(q: Mapping[str, Any], words: Mapping[str, Any]) -> str:
    """A quote's source: document, version, section and the stale flag when it has one."""
    source = f"{q['doc_id']} v{q['version']} · {q['section']}"
    if q["stale"]:
        source += f" · {words['labels']['stale']}"
    return source


def remediation_columns(words: Mapping[str, Any]) -> list[str]:
    labels = words["labels"]
    return [labels[k] for k in ("requirement", "missing", "owner_role", "due_date")]


def remediation_rows(group: Mapping[str, Any]) -> list[list[str]]:
    return [
        [i["requirement_id"], i["missing"], i["owner_role"], i["due_date"]] for i in group["items"]
    ]


TRAIL_COLUMNS = (
    "at",
    "who",
    "control",
    "requirement",
    "action",
    "verdict_before",
    "verdict_after",
    "note",
    "evidence",
    "model",
    "prompt_version",
)


def trail_columns(words: Mapping[str, Any]) -> list[str]:
    return [words["labels"][k] for k in TRAIL_COLUMNS]


def action_word(e: Mapping[str, Any], words: Mapping[str, Any]) -> str:
    """An entry's action in the report's words; a goal change's (#369) with its goals before and
    after filled in, a review round's (#402) with the round's name."""
    word: str = words["actions"][e["action"]]
    if e["action"] in ("round-start", "round-close"):
        return word.replace("{round}", e.get("round_name") or "")
    if e["action"] != "goal":
        return word
    goals = words["goals"]
    return word.format(before=goals[e["goal_before"]], after=goals[e["goal_after"]])


def trail_rows(entries: list[Mapping[str, Any]], words: Mapping[str, Any]) -> list[list[str]]:
    """The audit trail table's rows, newest first as given, in TRAIL_COLUMNS' order."""
    verdicts = words["verdicts"]
    return [
        [
            minute_of(e["at"]),
            e["reviewer_name"],
            e["control_id"],
            e["requirement_id"] or "",
            action_word(e, words),
            verdicts[e["verdict_before"]] if e["verdict_before"] else "",
            verdicts[e["verdict_after"]] if e["verdict_after"] else "",
            e["note"],
            ", ".join(f"{d['doc_id']} v{d['version']}" for d in e["evidence"]),
            e["model"],
            e["prompt_version"],
        ]
        for e in entries
    ]


# --- The Markdown export (#57) -----------------------------------------------------------------
#
# Each writer is `_md_*`, apart from the data builders above. Text carried from elsewhere (the
# company, questions, quotes, the lead's notes, reasons) stays as written, but never as Markdown
# or HTML of its own: `&`, `<` and `>` are escaped everywhere, a value in the head, a list or a
# table is kept to one line, and a table cell's `\` and `|` are escaped.


def markdown(report: Mapping[str, Any], words: Mapping[str, Any]) -> str:
    """The served report (`readiness_report`, redacted) as one Markdown file, in its `words`
    (`words_for`): the page's parts in the page's order, an empty list's sentence in its place.
    Carried text stays as written but can't become a heading, a table's edge or live HTML: a
    line break in the head, a list item or a table cell becomes a space; a quote stays a
    blockquote line by line. No model, no database."""
    labels, sentences = words["labels"], words["sentences"]
    parts = [
        f"# {words['title']}",
        _md_head(report["head"], words),
        f"## {labels['readiness']}",
        _md_readiness(report["readiness"], words),
        f"## {labels['controls']}",
        *_md_controls(report["controls"], words),
        f"## {labels['remediation']}",
        *(_md_remediation(report["remediation"], words) or [sentences["no_remediation"]]),
        f"## {labels['not_applicable']}",
        _md_not_applicable(report["not_applicable"], words) or sentences["no_not_applicable"],
        f"## {labels['trail']}",
        _md_trail(report["trail"]["entries"], words) or sentences["no_trail"],
        "---",
        _md_line(report["disclaimer"]),
    ]
    return "\n\n".join(parts) + "\n"


def _md_text(text: object) -> str:
    """Carried text with HTML's `&`, `<` and `>` escaped, so a viewer shows it, never runs it."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_line(text: object) -> str:
    """Carried text on one line (each line break a space), escaped as `_md_text`."""
    return " ".join(_md_text(text).splitlines())


def _md_cell(text: object) -> str:
    """Text for one table cell: on one line, a `\\` and a `|` escaped."""
    return _md_line(str(text).replace("\\", "\\\\").replace("|", "\\|"))


def _md_table(columns: list[str], rows: Iterable[list[str]]) -> str:
    lines = [f"| {' | '.join(_md_cell(c) for c in columns)} |", "|" + " --- |" * len(columns)]
    lines += [f"| {' | '.join(_md_cell(c) for c in row)} |" for row in rows]
    return "\n".join(lines)


def _md_head(head: Mapping[str, Any], words: Mapping[str, Any]) -> str:
    return "\n".join(f"- **{label}:** {_md_line(value)}" for label, value in head_rows(head, words))


def _md_readiness(r: Mapping[str, Any], words: Mapping[str, Any]) -> str:
    sentence, counts = readiness_lines(r, words)
    return "\n".join([sentence, "", *(f"- {count}" for count in counts)])


def _md_controls(controls: list[Mapping[str, Any]], words: Mapping[str, Any]) -> list[str]:
    """The controls table, then each accepted control's quotes under a heading of its own."""
    parts = [_md_table(control_columns(words), control_rows(controls, words))]
    for c in quoted(controls):
        parts.append(f"### {words['labels']['quotes']} {_md_line(c['id'])}")
        parts += [_md_quote(q, words) for q in c["quotes"]]
    return parts


def _md_quote(q: Mapping[str, Any], words: Mapping[str, Any]) -> str:
    """A quote as a blockquote, line by line as written, then its source."""
    lines = _md_text(q["quote"]).splitlines() or [""]
    source = _md_line(quote_source(q, words))
    return "\n".join([*(f"> {line}" for line in lines), ">", f"> — {source}"])


def _md_remediation(groups: list[Mapping[str, Any]], words: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    for group in groups:
        parts.append(f"### {_md_line(group['control_id'])} {_md_line(group['question'])}")
        parts.append(_md_table(remediation_columns(words), remediation_rows(group)))
    return parts


def _md_not_applicable(listed: list[Mapping[str, Any]], words: Mapping[str, Any]) -> str:
    reason = words["labels"]["reason"]
    return "\n".join(
        f"- **{_md_line(r['control_id'])} {_md_line(r['requirement_id'])}** "
        f"{_md_line(r['text'])}\n  - {reason}: {_md_line(r['reason'])}"
        for r in listed
    )


def _md_trail(entries: list[Mapping[str, Any]], words: Mapping[str, Any]) -> str:
    if not entries:
        return ""
    return _md_table(trail_columns(words), trail_rows(entries, words))
