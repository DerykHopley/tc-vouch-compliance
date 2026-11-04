"""vulnerability_status: Patch's vulnerability status, as implemented evidence.

Live Patch is out of scope for M1: the tool reads a recorded response, shaped like the
read-only tool Patch would offer (tracked CVEs, assets, priorities, overdue items,
risk-acceptance decisions), from the folder the compliance profile allows. The response is
an evidence document like any other (front matter with a version, kind `implemented`), so the
quote check reads it the same way.

Whatever goes wrong (no response, a malformed one, no answer in time) comes back as an
`evidence_pending` error, never an exception: the requirements wait for Patch.
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from vouch.documents import document_path
from vouch.front_matter import split_front_matter
from vouch.search import FOLDER_ESCAPED, ErrorResult, Passage, _passages

RESPONSE_ID = "patch-vulnerability-status"
EVIDENCE_PENDING = "evidence_pending"
# Patch can take about 90 seconds to wake from idle; a recorded response answers at once.
TIMEOUT_SECONDS: float = 120


class ResponseError(ValueError):
    """The recorded response can't be used; the message says why, in plain words."""


def read_response(folder: Path) -> list[Passage]:
    """The recorded response's passages: a versioned, implemented evidence document with text.
    Raises ResponseError when it is missing, unreadable or malformed."""
    path = document_path(folder, RESPONSE_ID)
    if path is None:
        raise ResponseError("there is no recorded response")
    try:
        data, _ = split_front_matter(path.read_text(encoding="utf-8"))
        passages = _passages(path)
    except UnicodeDecodeError as error:
        raise ResponseError("it isn't text") from error
    except OSError as error:
        raise ResponseError("it can't be opened") from error
    if data is None:
        raise ResponseError("it has no front matter")
    if not str(data.get("version") or "").strip():
        raise ResponseError("it has no version")
    if data.get("kind") != "implemented":
        raise ResponseError("its kind isn't implemented")
    if not passages:
        raise ResponseError("it holds no text")
    return passages


def vulnerability_status(
    folder: Path, *, timeout: float | None = None
) -> list[Passage] | ErrorResult:
    """Patch's response as passages, or a structured error; nothing is raised.

    `folder` is the patch folder the profile allows, resolved on load; one that now resolves
    elsewhere is refused with `folder_escaped`. Anything else that keeps the response from
    being read within `timeout` seconds (default TIMEOUT_SECONDS) is `evidence_pending`.
    """
    if folder.resolve() != folder:
        return ErrorResult(
            error=FOLDER_ESCAPED, detail="the folder now resolves outside where it was allowed"
        )
    limit = TIMEOUT_SECONDS if timeout is None else timeout
    # Looked up when called, so a test can stand in a slow or broken Patch.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="patch")
    try:
        return pool.submit(read_response, folder).result(timeout=limit)
    except FutureTimeout:
        return ErrorResult(
            error=EVIDENCE_PENDING, detail=f"Patch didn't answer within {limit:g} seconds"
        )
    except ResponseError as problem:
        return ErrorResult(
            error=EVIDENCE_PENDING,
            detail=f"Patch's vulnerability status could not be read ({problem})",
        )
    finally:
        pool.shutdown(wait=False)  # a late answer is never waited for
