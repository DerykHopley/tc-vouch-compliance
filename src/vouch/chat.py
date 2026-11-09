"""Vouch's chat (#44): the page the lead is on.

The agent itself, its prompt and its tools are vouch.chat_agent (#45). Its reply is sent whole,
once its quotes are checked (#46), and redacted whole (security layer 4, vouch.redaction).
"""

from dataclasses import dataclass
from typing import Literal

from vouch.isa import IsaControl

CHAT_PROMPT_VERSION = "chat-8"
"""The chat agent's prompt version, beside the assessments' (vouch.graph.PROMPT_VERSION): on the
developer page, and in the log line with each chat message's usage."""
CHAT_FAILED = "Vouch could not answer just now. Please try again shortly."
"""Must match NOT_ANSWERED in frontend/src/chat.ts: the one sentence for a chat that failed."""

Page = Literal["dossier", "paper", "developer", "report", "documents"]
"""The page the compliance lead is on: the Dossier overview, a control's working paper, the
developer page, the readiness report (#268), the Documents tab (#424)."""

_PAGE_NAMES: dict[Page, str] = {
    "dossier": "the Dossier overview (readiness, the 12 controls as cards, the evidence quotes)",
    "paper": "the working paper",
    "developer": "the developer page",
    "report": (
        "the readiness report (readiness, each control's reviewed verdict, the remediation items,"
        " the controls not applicable, the audit trail)"
    ),
    "documents": (
        "the Documents tab (each evidence document with its version, approval date, review"
        " cycle, whether it is stale, and the controls citing it)"
    ),
}


@dataclass(frozen=True)
class PageContext:
    """Where the compliance lead is: the page, and the control when one is open."""

    page: Page
    control: IsaControl | None = None

    def describe(self) -> str:
        where = _PAGE_NAMES[self.page]
        if self.control is None:
            return where
        return f"{where} of VDA ISA control {self.control.id}: “{self.control.question}”"
