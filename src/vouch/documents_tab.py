"""The Documents tab (#424): each evidence document a compliance profile uses, with what Vouch
reads from it (vouch.documents.DocumentSummary), whether it is stale, and the controls whose
newest assessment cites it; and the tab's own words, one table per language as the readiness
report's (vouch.report.WORDS). Apart from vouch.documents, which vouch.profile imports."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from vouch.added_documents import AddedDocument
from vouch.documents import DocumentSummary, by_title, document_summary
from vouch.dossier import cited_assessments
from vouch.graph import Assessment
from vouch.isa import IsaControl
from vouch.profile import ComplianceProfile
from vouch.report import DEFAULT_LANGUAGE, WORDS

# None of these claims an audit result (vouch.language.audit_claim). The stale word is the
# report's (WORDS' labels.stale), so a page and the report flag a document alike. The stale
# reasons name why a document's review date can't be worked out (documents.NO_REVIEW_DATE);
# an overdue review needs none, its approval date and cycle beside the flag say why.
DOCUMENT_WORDS: dict[str, dict[str, Any]] = {
    "en": {
        "title": "Documents",
        "sources": {"policies": "Policies", "records": "Records", "patch": "Patch"},
        "columns": {
            "document": "Document",
            "kind": "Kind",
            "version": "Version",
            "approved": "Approved",
            "review": "Review",
            "status": "Status",
            "cited_by": "Cited by",
            "origin": "Origin",
        },
        "kinds": {"documented": "Documented", "implemented": "Implemented"},
        "cycles": {
            "quarterly": "Quarterly",
            "half-yearly": "Half-yearly",
            "yearly": "Yearly",
            "two-yearly": "Two-yearly",
        },
        "stale": WORDS["en"]["labels"]["stale"],
        "stale_reasons": {
            "no_approval_date": "no approval date",
            "no_review_cycle": "no review cycle",
            "unknown_cycle": "unknown review cycle",
        },
        "current": "Current",
        "no_version": "no version",
        "no_date": "no date",
        "no_cycle": "none",
        "not_cited": "Not cited yet",
        "no_documents": "This profile has no evidence documents.",
        "origins": {"built-in": "Built in", "added": "Added"},
        "withdrawn": "Withdrawn",
    },
    "de": {
        "title": "Dokumente",
        "sources": {"policies": "Richtlinien", "records": "Aufzeichnungen", "patch": "Patch"},
        "columns": {
            "document": "Dokument",
            "kind": "Art",
            "version": "Version",
            "approved": "Freigegeben am",
            "review": "Überprüfung",
            "status": "Status",
            "cited_by": "Zitiert von",
            "origin": "Herkunft",
        },
        "kinds": {"documented": "Dokumentiert", "implemented": "Umgesetzt"},
        "cycles": {
            "quarterly": "Vierteljährlich",
            "half-yearly": "Halbjährlich",
            "yearly": "Jährlich",
            "two-yearly": "Zweijährlich",
        },
        "stale": WORDS["de"]["labels"]["stale"],
        "stale_reasons": {
            "no_approval_date": "kein Freigabedatum",
            "no_review_cycle": "kein Prüfzyklus",
            "unknown_cycle": "unbekannter Prüfzyklus",
        },
        "current": "Aktuell",
        "no_version": "keine Version",
        "no_date": "kein Datum",
        "no_cycle": "keine",
        "not_cited": "Noch nicht zitiert",
        "no_documents": "Dieses Profil hat keine Nachweisdokumente.",
        "origins": {"built-in": "Mitgeliefert", "added": "Hinzugefügt"},
        "withdrawn": "Zurückgezogen",
    },
}


def document_words(language: str) -> tuple[str, dict[str, Any]]:
    """The tab's own words for an answer language (ISO 639-1), with the language they are in:
    itself when DOCUMENT_WORDS has a table for it, else English, as the report's words fall
    back. The page's `lang` is that language, so it always matches the words."""
    chosen = language if language in DOCUMENT_WORDS else DEFAULT_LANGUAGE
    return chosen, DOCUMENT_WORDS[chosen]


@dataclass(frozen=True)
class ProfileDocument:
    """A document the profile uses, as the tab lists it: what Vouch read from it, whether it is
    stale, the controls (in control order) whose newest assessment cites it, and whether a lead
    added it (#585) or it is built in (a profile's fixtures, the sample library)."""

    summary: DocumentSummary
    stale: bool
    cited_by: tuple[str, ...]
    added: bool = False
    withdrawn: bool = False
    """An added document the lead withdrew (#586): listed, no longer searched or cited."""

    def as_dict(self) -> dict[str, Any]:
        """The summary's fields with `stale`, `cited_by`, `origin` (`added` or `built-in`) and
        `withdrawn`: how the API sends it."""
        return asdict(self.summary) | {
            "stale": self.stale,
            "cited_by": list(self.cited_by),
            "origin": "added" if self.added else "built-in",
            "withdrawn": self.withdrawn,
        }


def added_summary(document: AddedDocument, today: date) -> DocumentSummary:
    """What the tab shows of an added document's version, judged on `today`: the form's fields
    and the version Vouch set. Its language isn't asked for, so the page's applies."""
    facts = document.facts(today)
    return DocumentSummary(
        id=document.doc_id,
        source=document.source,
        title=document.title,
        kind=facts.kind,
        version=str(document.version),
        approval_date=document.approval_date.isoformat(),
        review_cycle=document.review_cycle,
        stale_because=facts.stale_because,
        language=None,
    )


def _cited_by(
    controls: Sequence[IsaControl], latest: Mapping[str, Assessment]
) -> dict[str, list[str]]:
    """Each cited document id, with the controls (in `controls` order, each once) whose newest
    assessment cites it; a rejected one counts for nothing, as in the overview's quotes (#147)."""
    found: dict[str, list[str]] = {}
    for control, assessment in cited_assessments(controls, latest):
        cited = {c.doc_id for r in assessment.requirements for c in r.citations}
        for doc_id in cited:
            found.setdefault(doc_id, []).append(control.id)
    return found


def profile_documents(
    profile: ComplianceProfile,
    controls: Sequence[IsaControl],
    latest: Mapping[str, Assessment],
    today: date,
    added: Sequence[AddedDocument] = (),
) -> list[ProfileDocument]:
    """Each document the profile uses: its sources in the profile's order, each source's
    documents it may use (EvidenceSource.usable_documents) with its `added` documents (#585,
    the newest version of each, a withdrawn one marked `withdrawn`, #586), by title as the
    library is listed, then a source only added documents use; each a DocumentSummary judged
    on `today`, with `stale` and `cited_by`. A citation has no source, so a cited id matches
    every listed document with that id. An unreadable folder or document raises (OSError, or
    ValueError for one that isn't UTF-8 text)."""
    cited_by = _cited_by(controls, latest)
    added_ids = {(d.source, d.doc_id) for d in added}
    withdrawn_ids = {(d.source, d.doc_id) for d in added if d.withdrawn_at is not None}
    by_source: dict[str, list[DocumentSummary]] = {}
    for name, source in profile.evidence_sources.items():
        by_source[name] = [document_summary(p, name, today) for p in source.usable_documents()]
    for document in added:
        by_source.setdefault(document.source, []).append(added_summary(document, today))
    return [
        ProfileDocument(
            summary=summary,
            stale=summary.stale_because is not None,
            cited_by=tuple(cited_by.get(summary.id, [])),
            added=(summary.source, summary.id) in added_ids,
            withdrawn=(summary.source, summary.id) in withdrawn_ids,
        )
        for summaries in by_source.values()
        for summary in by_title(summaries)
    ]
