"""vulnerability_status: Patch's vulnerability status, as implemented evidence.

Live Patch is out of scope for M1: the tool reads a recorded response, shaped like the
read-only tool Patch would offer (tracked CVEs, assets, priorities, overdue items,
risk-acceptance decisions), from the folder the compliance profile allows. The response is
an evidence document like any other (front matter with a version, kind `implemented`), so the
quote check reads it the same way.

Whatever goes wrong comes back as an error result, never an exception: `timed_out` when Patch
doesn't answer in time, `evidence_pending` for anything else (no response, a malformed one,
an error while reading). Either way the evidence waits for Patch.
"""

import logging
import threading
from pathlib import Path
from typing import Any

from vouch.documents import document_path, escaped
from vouch.front_matter import split_front_matter
from vouch.search import ErrorResult, Passage, folder_escaped, passages_of

RESPONSE_ID = "patch-vulnerability-status"
EVIDENCE_PENDING = "evidence_pending"
TIMED_OUT = "timed_out"  # also pending; asking again in the same run would only wait again
# Patch can take about 90 seconds to wake from idle; a recorded response answers at once.
TIMEOUT_SECONDS: float = 120
UNREADABLE = "Patch's vulnerability status could not be read"

log = logging.getLogger(__name__)


class ResponseError(ValueError):
    """The recorded response can't be used; the message says why, in plain words."""


def read_response(folder: Path) -> list[Passage]:
    """The recorded response's passages: a versioned, implemented evidence document with text,
    read once. Raises ResponseError when it is missing, unreadable or malformed."""
    path = document_path(folder, RESPONSE_ID)
    if path is None:
        raise ResponseError("there is no recorded response")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ResponseError("it isn't text") from error
    except OSError as error:
        raise ResponseError("it can't be opened") from error
    data, body = split_front_matter(text)
    if data is None:
        raise ResponseError("it has no front matter")
    if not str(data.get("version") or "").strip():
        raise ResponseError("it has no version")
    if data.get("kind") != "implemented":
        raise ResponseError("its kind isn't implemented")
    passages = passages_of(path.stem, body)
    if not passages:
        raise ResponseError("it holds no text")
    return passages


def vulnerability_status(
    folder: Path, *, timeout: float | None = None
) -> list[Passage] | ErrorResult:
    """Patch's response as passages, or an error result; nothing is raised.

    `folder` is the patch folder the profile allows; one that has `escaped` is refused with
    `folder_escaped`. The read runs in a daemon thread, so one that hangs past `timeout`
    seconds (default TIMEOUT_SECONDS) is left behind, `timed_out`, and never keeps the process
    from exiting.
    """
    if escaped(folder):
        return folder_escaped()
    limit = TIMEOUT_SECONDS if timeout is None else timeout
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def read() -> None:
        try:
            outcome["passages"] = read_response(folder)  # looked up now, so a test can stand in
        except Exception as error:  # noqa: BLE001 - every failure is pending evidence
            outcome["error"] = error
        finally:
            done.set()

    try:
        threading.Thread(target=read, name="patch-read", daemon=True).start()
    except RuntimeError as error:  # no thread to be had
        outcome["error"] = error
        done.set()
    if not done.wait(limit):
        return ErrorResult(error=TIMED_OUT, detail=f"Patch didn't answer within {limit:g} seconds")
    error = outcome.get("error")
    if error is None:
        return outcome["passages"]
    if isinstance(error, ResponseError):
        return ErrorResult(error=EVIDENCE_PENDING, detail=f"{UNREADABLE} ({error})")
    log.warning("reading Patch's vulnerability status failed", exc_info=error)
    return ErrorResult(
        error=EVIDENCE_PENDING, detail=f"{UNREADABLE} (the read failed unexpectedly)"
    )
