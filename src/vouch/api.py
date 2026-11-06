"""The HTTP API: health, one control's assessment streamed as server-sent events up to the
compliance lead's review, the review that resumes it, the lead's remediation decisions, the
user's assessments, the Dossier overview, and the developer page's settings.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`. With VOUCH_FRONTEND naming the
built front end (frontend/dist), it serves that too, on one origin, as the container does.
"""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field, StringConstraints

from vouch import audit, remediation, report
from vouch.allow_list import allowed_tools
from vouch.auth import (
    ClerkAuth,
    ClerkUnavailable,
    ClerkUser,
    NotAMember,
    Role,
    SignedOut,
    SignInAgain,
)
from vouch.chat import CHAT_FAILED, CHAT_PROMPT_VERSION, Page, PageContext
from vouch.chat_agent import (
    GREETING_REQUEST,
    ChatTools,
    chat_context,
    chat_graph,
    chat_prompt,
    chat_turn,
)
from vouch.database import Connection, Database, DatabaseUnavailable
from vouch.dossier import PaperRequirement, overview, requirements_by_control
from vouch.graph import (
    Assessment,
    InvalidReviewError,
    NotWaitingForReviewError,
    ReviewDecision,
    UnknownAssessmentError,
    load_assessment,
    new_thread_id,
    state_serializer,
)
from vouch.isa import (
    BaseFrameworkError,
    IsaControl,
    UnknownControlError,
)
from vouch.limits import TOO_MANY_REQUESTS, MessageLimit, RateLimit
from vouch.memory import (
    Memory,
    newest_by_control,
    process_checkpointer,
    process_store,
)
from vouch.model_choice import (
    MODEL_ID,
    CatalogueUnavailable,
    ModelChoice,
    NoKey,
    UnknownModel,
    model_store,
)
from vouch.profile import (
    DEFAULT_PROFILE,
    BrokenProfile,
    ComplianceProfile,
    ProfileScan,
    scan_profiles,
)
from vouch.redaction import install_log_redaction, redact_data, redacted_event, remember_secrets
from vouch.runs import (
    ASSESSMENT_FAILED,
    RUNNING_ALREADY,
    AlreadyRunning,
    AssessmentRun,
    RunningAssessments,
    assessment_events,
    begin_run,
)
from vouch.settings import (
    DATABASE_URL_ENV,
    DEFAULT_USER,
    FRONTEND_ENV,
    PROFILES_DIR_ENV,
    Settings,
    SettingsError,
    User,
    load_settings,
)
from vouch.spending import (
    CHAT,
    MAX_DAILY_LIMIT,
    DailyLimitReached,
    NoSharedCounter,
    SpendingGuard,
    Usage,
    UsageMeter,
    count_assessment,
    spending_store,
)

log = logging.getLogger(__name__)

SIGNED_OUT = "Sign in to use Vouch."
CLERK_UNAVAILABLE = "Vouch cannot check your sign-in just now. Please try again shortly."
DEVELOPERS_ONLY = "This is available to developers only."
NOT_A_MEMBER = (
    "Vouch is open to members of the course's Discord server. This Discord account is not in it."
)
SIGN_IN_AGAIN = "Please sign in with Discord again, so Vouch can check your Discord servers."
DATABASE_UNAVAILABLE = "The database is unavailable just now. Please try again shortly."
UNKNOWN_PROFILE = "There is no such compliance profile."
OVERVIEW_FAILED = "The overview could not be loaded just now. Please try again."
REPORT_FAILED = "The readiness report could not be loaded just now. Please try again."
EXPORT_FAILED = "The readiness report could not be exported just now. Please try again."
NO_KEY = (
    "No OpenRouter key is set, so assessments run on the fake model and a model id cannot be "
    "checked. Set OPENROUTER_API_KEY to choose a model."
)
UNKNOWN_MODEL = "OpenRouter has no model “{model}”. Check the id on openrouter.ai/models."
CATALOGUE_UNAVAILABLE = (
    "OpenRouter cannot be reached to check the model just now. Please try again shortly."
)


class AssessmentRequest(BaseModel):
    control_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    note: str = ""  # the lead's note from a rejected review, when the control is assessed again


@dataclass(frozen=True)
class RequestedAssessment:
    control: IsaControl
    note: str


PROFILE_HEADER = "X-Vouch-Profile"
"""Names the compliance profile a request is for (#47): the top bar's profile switcher sets it on
every call. Without it, the default profile."""


def app_settings(request: Request) -> Settings:
    """The Settings the app was created with."""
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(app_settings)]


def _signed_out() -> HTTPException:
    return HTTPException(status_code=401, detail=SIGNED_OUT, headers={"WWW-Authenticate": "Bearer"})


def current_user(request: Request, settings: AppSettings) -> User:
    """The request's user: local mode's default user; in clerk mode, the verified token's (auth.py).

    Checked offline, so Clerk's API is never asked. No valid token is a 401.
    """
    if settings.auth_mode == "local":
        return DEFAULT_USER
    auth: ClerkAuth = request.app.state.auth
    try:
        return auth.user(request)
    except SignedOut:
        raise _signed_out() from None


CurrentUser = Annotated[User, Depends(current_user)]


def app_database(request: Request) -> Database | None:
    """The app's Database; None when no database URL is set."""
    return request.app.state.database


AppDatabase = Annotated[Database | None, Depends(app_database)]


def checkpointer(db: AppDatabase) -> Iterator[BaseCheckpointSaver]:
    """Where assessments wait for review (#38): Postgres with a database URL, so they outlast a
    restart; else this process's memory. A failure of the database is a 503."""
    if db is None:
        yield process_checkpointer()
        return
    with db.checkpointer(state_serializer()) as saver:
        yield saver


Checkpointer = Annotated[BaseCheckpointSaver, Depends(checkpointer)]


def database_connection(db: AppDatabase) -> Iterator[Connection]:
    """A connection for a route that needs the database. A 503 (database_unavailable) when
    there's none to be had, or when it drops while the route uses it."""
    if db is None:
        raise DatabaseUnavailable("no database URL is set")
    with db.connection() as conn:
        yield conn


def database_unavailable(request: Request, error: Exception) -> JSONResponse:
    """Any route: the database can't be used just now is a 503, its reason only in the log.

    `unavailable: "database"` tells the front end which 503 this is: on the developer API it
    comes only after require_developer passed, so the page shows saying so. Any other 503 (the
    role lookup's, when Clerk can't be asked) leaves the role unknown, and the page hidden."""
    content = {"detail": DATABASE_UNAVAILABLE, "unavailable": "database"}
    return JSONResponse(status_code=503, content=content)


def _clerk_user(request: Request, user: User, settings: Settings) -> ClerkUser | None:
    """Clerk's lookup of the user (role and display name, cached, auth.py); None in local mode,
    where Clerk isn't asked. A user Clerk doesn't know is a 401. ClerkUnavailable is raised for
    the caller to answer."""
    if settings.auth_mode == "local":
        return None
    auth: ClerkAuth = request.app.state.auth
    try:
        return auth.lookup(user)
    except SignedOut:
        raise _signed_out() from None


def user_role(request: Request, user: CurrentUser, settings: AppSettings) -> Role:
    """The user's role, for a route that needs it. Local mode's one user is a developer: local
    mode is for a developer's machine. In clerk mode Clerk's API is asked; when it can't
    answer, a 503, never access."""
    try:
        found = _clerk_user(request, user, settings)
    except ClerkUnavailable:
        raise HTTPException(status_code=503, detail=CLERK_UNAVAILABLE) from None
    return "developer" if found is None else found.role


def reviewer_name(request: Request, user: CurrentUser, settings: AppSettings) -> str:
    """The user's display name as it is now, for their audit entries (#154): local mode's
    setting; in clerk mode Clerk's, from the role's lookup. When Clerk can't be asked, the user
    id, so the review still goes on."""
    try:
        found = _clerk_user(request, user, settings)
    except ClerkUnavailable:
        log.warning("the reviewer's name lookup failed: the entry keeps the user id as the name")
        return user.id
    return settings.local_user_name if found is None else found.name


ReviewerName = Annotated[str, Depends(reviewer_name)]


def require_developer(role: Annotated[Role, Depends(user_role)]) -> None:
    """A route only a developer may use (the developer page, #34); anyone else gets a 403."""
    if role != "developer":
        raise HTTPException(status_code=403, detail=DEVELOPERS_ONLY)


def require_access(request: Request, user: CurrentUser, settings: AppSettings) -> None:
    """With the course's Discord server set (#152), a user outside it, and not invited, gets a
    403 with `reason: "not-a-member"`; one whose Discord token can't be used, a 403 with
    `reason: "sign-in-again"`. Clerk or Discord failing is a 503, never access (auth.py)."""
    if settings.auth_mode == "local":
        return
    auth: ClerkAuth = request.app.state.auth
    try:
        auth.access(user)
    except SignedOut:
        raise _signed_out() from None
    except ClerkUnavailable:
        raise HTTPException(status_code=503, detail=CLERK_UNAVAILABLE) from None
    except NotAMember:
        raise _refused(NOT_A_MEMBER, "not-a-member") from None
    except SignInAgain:
        raise _refused(SIGN_IN_AGAIN, "sign-in-again") from None


class _Refused(HTTPException):
    """A 403 whose body also carries a reason code the page acts on."""


def _refused(detail: str, reason: str) -> HTTPException:
    return _Refused(status_code=403, detail={"detail": detail, "reason": reason})


def refused(request: Request, error: Exception) -> JSONResponse:
    """A _Refused 403 as `{"detail": ..., "reason": ...}`, beside FastAPI's own `detail`."""
    assert isinstance(error, _Refused)
    return JSONResponse(status_code=403, content=error.detail)


def rate_limit(request: Request, user: CurrentUser) -> None:
    """Security layer 1: a user over the request rate gets a 429 with a Retry-After."""
    wait = request.app.state.rate_limit.wait_for(user.id)
    if wait is not None:
        headers = {"Retry-After": str(wait)}
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS, headers=headers)


def user_profiles(settings: Settings, user: User) -> ProfileScan:
    """The one place that decides which compliance profiles a user may see and use (#47): for
    now every profile in the profiles folder, for every user, each read once. A broken one is
    left out (and listed for the developer page), never a failure."""
    return scan_profiles(settings.profiles_dir)


def compliance_profile(
    settings: AppSettings,
    user: CurrentUser,
    chosen: Annotated[str | None, Header(alias=PROFILE_HEADER)] = None,
) -> ComplianceProfile:
    """The profile the request names, else the default, from the user's profiles. Settled
    before the stream starts: one the user doesn't have is a 404; a broken one a plain 500."""
    name = chosen or DEFAULT_PROFILE
    found = user_profiles(settings, user).find(name)
    if found is None:
        raise HTTPException(status_code=404, detail=UNKNOWN_PROFILE)
    if isinstance(found, BrokenProfile):
        log.error("the compliance profile %s can't be used: %s", name, found.problem)
        raise HTTPException(status_code=500, detail=ASSESSMENT_FAILED)
    return found


Profile = Annotated[ComplianceProfile, Depends(compliance_profile)]


def _unreadable_edition(profile: ComplianceProfile, detail: str) -> HTTPException:
    """The plain 500 for a profile whose ISA edition can't be read, logged with the error
    being handled (the response names no file); the caller raises it."""
    log.exception("the ISA edition for %s could not be read", profile.answer_language)
    return HTTPException(status_code=500, detail=detail)


def requested_assessment(
    request: Annotated[AssessmentRequest, Body()], profile: Profile
) -> RequestedAssessment:
    """The control is looked up, in the request's profile's ISA edition (#48), before the
    stream starts, so an unknown one is a plain 404 and an unreadable edition a plain 500, not
    an event."""
    try:
        return RequestedAssessment(profile.isa_control(request.control_id), request.note)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BaseFrameworkError:
        raise _unreadable_edition(profile, ASSESSMENT_FAILED) from None


def profile_tools(profile: Profile) -> frozenset[str]:
    """The tools the API lets the model call: the compliance profile's allow-list (security
    layer 2, vouch.allow_list), the one the graph's own guard checks too."""
    return allowed_tools(profile)


def user_memory(db: AppDatabase, user: CurrentUser, profile: Profile) -> Iterator[Memory]:
    """The user's long-term memory under the compliance profile (#38): Postgres's store with a
    database URL, else this process's. A failure of the database is a 503."""
    if db is None:
        yield Memory(process_store(), user.id, profile.id)
        return
    with db.store() as store:
        yield Memory(store, user.id, profile.id)


UserMemory = Annotated[Memory, Depends(user_memory)]


def audit_trail(db: AppDatabase) -> audit.AuditTrail:
    """Where reviewed verdicts and overrides are recorded (#39): Postgres with a database URL,
    else this process."""
    return audit.process_trail() if db is None else audit.PostgresTrail(db)


AuditTrail = Annotated[audit.AuditTrail, Depends(audit_trail)]


def remediation_items(db: AppDatabase) -> remediation.RemediationItems:
    """Where the lead's remediation decisions are kept, with their audit entries (#43): Postgres
    with a database URL, else this process."""
    return remediation.process_items() if db is None else remediation.PostgresItems(db)


RemediationItems = Annotated[remediation.RemediationItems, Depends(remediation_items)]


def assessment_data(
    assessment: Assessment, decided: dict[remediation.Key, remediation.DecidedItem] | None = None
) -> dict[str, Any]:
    """The assessment as the API sends it, each remediation item with its status (#43): a draft,
    or as the lead decided it (`decided`, none for a run just made or just reviewed)."""
    return assessment.to_dict() | {
        "remediation": remediation.shown_items(assessment, decided or {})
    }


public = APIRouter(prefix="/api")
"""The routes open to anyone: health alone."""

router = APIRouter(
    prefix="/api",
    dependencies=[Depends(current_user), Depends(rate_limit), Depends(require_access)],
)
"""Every other route: a route added here needs a signed-in user in clerk mode, within the rate,
and, with the course's Discord server set, one who may use Vouch (#152)."""


@public.get("/health")
def health(settings: AppSettings, db: AppDatabase) -> dict[str, Any]:
    """Also tells the front end how to sign in (Clerk's publishable key is public), and, with a
    database URL set, whether the database is reachable, set up by vouch-db-setup (#123).
    `status` is liveness: ok while the app answers, whatever the database says."""
    publishable_key = settings.clerk.publishable_key if settings.clerk else None
    # Who may sign in, for the signed-out page's words: the course's Discord server (#152), or
    # Clerk's own restrictions; local mode signs nobody in.
    gate = "none"
    if settings.clerk is not None:
        gate = "discord-server" if settings.clerk.discord_server_id else "clerk"
    auth = {"mode": settings.auth_mode, "publishable_key": publishable_key, "gate": gate}
    answer: dict[str, Any] = {"status": "ok", "version": version("vouch"), "auth": auth}
    if db is not None:
        answer["database"] = db.health()
    return answer


def app_models(request: Request) -> ModelChoice:
    """Which model an assessment runs on (vouch.model_choice)."""
    return request.app.state.models


AppModels = Annotated[ModelChoice, Depends(app_models)]


def chosen_model(models: AppModels) -> str | None:
    """The model id the assessment runs on (None: the fake), read before the stream starts: a
    stored choice that can't be read is a plain 503, and no other paid model runs instead."""
    return models.chosen()


def app_spending(request: Request) -> SpendingGuard:
    """The daily limit and usage (vouch.spending)."""
    return request.app.state.spending


AppSpending = Annotated[SpendingGuard, Depends(app_spending)]


def running_assessments(request: Request) -> RunningAssessments:
    """Whose assessments are running in this process (vouch.runs)."""
    return request.app.state.running


Running = Annotated[RunningAssessments, Depends(running_assessments)]


def within_daily_limit(user: User, spending: SpendingGuard, chosen: str | None) -> None:
    """Counts the assessment against the user's daily limit before the stream starts
    (vouch.spending.count_assessment). Over the limit, a 429 saying when it resets
    (daily_limit_reached); the counter unavailable, or only in memory for a paid model, a
    503."""
    try:
        count_assessment(spending, user.id, paid=chosen is not None)
    except NoSharedCounter as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


def new_assessment(
    requested: Annotated[RequestedAssessment, Depends(requested_assessment)],
    profile: Profile,
    chosen: Annotated[str | None, Depends(chosen_model)],
    user: CurrentUser,
    spending: AppSpending,
    memory: UserMemory,
    running: Running,
) -> Iterator[str]:
    """A new thread for the assessment, before the stream starts, only once the control, the
    compliance profile and the model are known (FastAPI skips a dependency whose own
    dependencies failed, so a 422, a 404, a broken profile's 500 or the model choice's 503
    takes no count): the lead's one run (vouch.runs.begin_run, as the chat starts them),
    remembered (#38), then counted (within_daily_limit). Another of the lead's runs going is a
    409; a write that fails is a 503 with nothing counted; a count refused removes what was
    remembered. No model runs either way. The lead's hold is released once the response has
    ended."""
    try:
        thread_id = begin_run(
            running,
            memory,
            requested.control.id,
            count=lambda: within_daily_limit(user, spending, chosen),
        )
    except AlreadyRunning:
        raise HTTPException(status_code=409, detail=RUNNING_ALREADY) from None
    try:
        yield thread_id
    finally:
        running.release(memory.scope)


def assessment_run(
    models: AppModels,
    chosen: Annotated[str | None, Depends(chosen_model)],
    tools: Annotated[frozenset[str], Depends(profile_tools)],
    saver: Checkpointer,
    memory: UserMemory,
) -> AssessmentRun:
    """What the request's assessment runs on (vouch.runs.AssessmentRun), metered afresh."""
    return AssessmentRun(models, chosen, tools, saver, UsageMeter(chosen), memory.scope)


def record_off_the_event_loop(
    spending: SpendingGuard, user_id: str, control_id: str, usage: Usage
) -> None:
    """Keep the usage without blocking the event loop. The stream's generator records as it
    ends, in the threadpool that runs it; but a stream the client abandons is closed when it's
    collected, which may be on the event loop, where a slow database would stall every request
    for up to the pool's timeout. There, a thread of its own records it."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:  # no loop in this thread: the threadpool, or the CLI
        spending.record(user_id, control_id, usage)
        return
    thread = threading.Thread(
        target=spending.record, args=(user_id, control_id, usage), name="vouch-usage", daemon=True
    )
    thread.start()


def daily_limit_reached(request: Request, error: Exception) -> JSONResponse:
    """The daily limit's 429: `limit: "daily"` tells it apart from the request rate's."""
    assert isinstance(error, DailyLimitReached)
    content = {
        "detail": error.message(),
        "limit": "daily",
        "resets_at": error.resets_at.isoformat(),
    }
    headers = {"Retry-After": str(error.retry_after)}
    return JSONResponse(status_code=429, content=content, headers=headers)


@router.post("/assessments", response_class=EventSourceResponse)
def create_assessment(
    requested: Annotated[RequestedAssessment, Depends(requested_assessment)],
    profile: Profile,
    run: Annotated[AssessmentRun, Depends(assessment_run)],
    # Held, remembered and counted only once the control, the profile and the model passed
    # (new_assessment depends on each).
    thread_id: Annotated[str, Depends(new_assessment)],
    memory: UserMemory,
    user: CurrentUser,
    spending: AppSpending,
) -> Iterable[ServerSentEvent]:
    """A `step` event per agent step, a `requirement` event per judged requirement, one
    `result` (the proposed Assessment, waiting for review, with the `thread_id` to review it
    by), then close; or one `error`, then close. Each request is a new assessment; with a
    `note`, a rejected control assessed again, the note passed to the model.

    The model is vouch.model_choice's: OpenRouter when a key is set (in clerk mode only a
    signed-in user gets here), else the scripted fake. A failure after retries, or a run past
    its call budget, is the `error`.

    Every tool call the model makes is checked against the compliance profile's allow-list here
    too (security layer 2): a refused one is a `guard` step. The call budget sits inside, on the
    model itself, so it counts every call made to OpenRouter, refused tool calls included.

    The `result` carries the assessment's `usage`, its tokens and cost (vouch.spending), metered
    on every model call (the judge's and the remediation drafts' included), which is kept
    whether the assessment ends in its result or an `error`: the calls were made. Every event
    is redacted (security layer 4).
    """
    control = requested.control
    try:
        events = assessment_events(
            control, profile, run, memory, thread_id=thread_id, note=requested.note
        )
        for event, data in events:
            if isinstance(data, Assessment):
                data = {"assessment": assessment_data(data), "usage": run.meter.usage().to_dict()}
            for name, clean in redacted_event(event, data):
                yield ServerSentEvent(event=name, data=clean)
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("assessment of %s failed", control.id)
        yield ServerSentEvent(event="error", data={"message": ASSESSMENT_FAILED})
    finally:
        record_off_the_event_loop(spending, user.id, control.id, run.meter.usage())


@router.get("/assessments")
def list_assessments(memory: UserMemory, saver: Checkpointer) -> dict[str, Any]:
    """The user's assessments under the compliance profile, newest first: each one's thread id,
    control, status and verdict. Another user's are never listed."""
    summaries = [
        {
            "thread_id": a.thread_id,
            "control_id": a.control_id,
            "status": a.status,
            "verdict": a.verdict,
        }
        for a in memory.assessments(saver)
    ]
    return {"assessments": summaries}


@router.get("/assessments/{thread_id}")
def open_assessment(
    thread_id: str, memory: UserMemory, saver: Checkpointer, items: RemediationItems
) -> dict[str, Any]:
    """One of the user's assessments, proposed or reviewed, redacted as the stream's events are.
    404 for an unknown thread, another user's included."""
    try:
        assessment = load_assessment(thread_id, saver, scope=memory.scope)
    except UnknownAssessmentError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    decided = items.decided(memory.user_id, memory.profile_id, [thread_id])
    clean, _ = redact_data({"assessment": assessment_data(assessment, decided)})
    return clean


@router.post("/assessments/{thread_id}/review")
def review_assessment(
    thread_id: str,
    decision: Annotated[ReviewDecision, Body()],
    trail: AuditTrail,
    memory: UserMemory,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """Resume a waiting assessment with the lead's decision; the reviewed Assessment, redacted
    as the stream's events are (its state holds the model's words as written). No model runs.
    The decision and its audit entries are written together (vouch.audit): when the entries
    can't be, nothing is, and it's a 503.

    404 for an unknown thread, another user's included; 409 for one not waiting (reviewed
    already); 422 for an override naming a requirement the assessment didn't judge, or naming
    one twice (the decision's shape is checked before, also 422)."""
    try:
        reviewed = audit.review(
            trail, thread_id, decision, reviewer=audit.Reviewer(user.id, name), scope=memory.scope
        )
    except UnknownAssessmentError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NotWaitingForReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidReviewError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:  # the review stands whatever happens here: noting when is a convenience
        memory.note_worked()
    except Exception:
        log.exception("a review's time wasn't kept")
    clean, _ = redact_data({"assessment": assessment_data(reviewed)})
    return clean


@router.post("/assessments/{thread_id}/remediation/{requirement_id}")
def decide_remediation(
    thread_id: str,
    requirement_id: str,
    request: Annotated[remediation.DecisionRequest, Body()],
    items: RemediationItems,
    memory: UserMemory,
    saver: Checkpointer,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead approves or discards one of an accepted assessment's remediation items (#43),
    an approval maybe with another owner role or due date: the item as kept, redacted. The
    decision and its audit entry are kept together, under the user and the compliance profile;
    when the entry can't be written, neither is (a 503). The same decision again changes
    nothing and writes no entry.

    404 for an unknown thread (another user's or profile's too) or a requirement with no item;
    409 for an assessment not accepted, an item decided the other way, or approved with other
    values (`detail` names the kept ones); 422 for an approval due on a date past everywhere or
    beyond the horizon, or naming no owner where Vouch suggested none, with the hint in `detail`
    (the request's shape is checked before, also 422)."""
    try:
        kept = remediation.decide(
            items,
            saver,
            thread_id,
            requirement_id,
            request,
            reviewer=audit.Reviewer(user.id, name),
            profile_id=memory.profile_id,
            scope=memory.scope,
        )
    except (UnknownAssessmentError, remediation.UnknownItemError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (remediation.NotAcceptedError, remediation.DecidedAlreadyError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (remediation.DueDateError, remediation.OwnerNeededError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    clean, _ = redact_data({"item": kept.shown()})
    return clean


@router.get("/audit-trail")
def read_audit_trail(
    trail: AuditTrail,
    user: CurrentUser,
    profile: Profile,
    limit: Annotated[int, Query(ge=1, le=audit.MAX_PAGE)] = audit.DEFAULT_PAGE,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    """A page of the user's audit entries under the compliance profile, newest first, redacted
    as every answer is: at most `limit`, after the entry with id `before`; `next` is the
    `before` for the page after (null: no more). The trail is append-only: no route changes or
    removes an entry."""
    page = audit.read(trail, user.id, profile.id, limit=limit, before=before)
    clean, _ = redact_data({"entries": [entry.to_dict() for entry in page.entries]})
    return clean | {"next": page.next}


@router.get("/profiles")
def list_profiles(settings: AppSettings, user: CurrentUser) -> dict[str, Any]:
    """The compliance profiles the user may switch between (#47): each one's id, company,
    country and answer language. A profile that can't be loaded is left out (the developer page
    names it). `default` is the profile a request without X-Vouch-Profile is under; null when
    that one is left out."""
    scan = user_profiles(settings, user)
    listed = [
        {
            "id": p.id,
            "company": p.company,
            "country": p.country,
            "answer_language": p.answer_language,
        }
        for p in scan.loaded
    ]
    default = scan.find(DEFAULT_PROFILE)
    return {"profiles": listed, "default": default.id if default in scan.loaded else None}


_paper_requirements: dict[str, tuple[ComplianceProfile, Any]] = {}
"""Each profile's requirements, redacted, by profile id, with the profile object they were built
from (#47: one entry per profile, so a switch neither rebuilds nor mixes them)."""


def paper_requirements(
    profile: ComplianceProfile, controls: Iterable[IsaControl]
) -> dict[str, list[PaperRequirement]]:
    """Each of `controls`' requirements under the profile (vouch.dossier.requirements_by_control),
    redacted as the overview is; built once per loaded profile, as they change only with it."""
    kept = _paper_requirements.get(profile.id)
    if kept is None or kept[0] is not profile:
        built = requirements_by_control(profile, controls)
        clean, _ = redact_data(built)
        kept = _paper_requirements[profile.id] = (profile, clean)
    return kept[1]


def _standing(
    profile: ComplianceProfile, memory: Memory, saver: BaseCheckpointSaver, failed: str
) -> tuple[list[IsaControl], dict[str, Assessment], list[str]]:
    """What the overview and the report are built from: the profile's controls in its ISA
    edition (unreadable: the plain 500 with `failed`), each control's newest assessment, and the
    accepted ones' thread ids (whose remediation decisions count)."""
    latest = newest_by_control(memory, saver)  # from each control's newest thread
    accepted = [a.thread_id for a in latest.values() if a.status == "accepted"]
    try:
        controls = profile.isa_edition().controls
    except BaseFrameworkError:
        raise _unreadable_edition(profile, failed) from None
    return list(controls), latest, accepted


@router.get("/overview")
def dossier_overview(
    profile: Profile, memory: UserMemory, saver: Checkpointer, items: RemediationItems
) -> dict[str, Any]:
    """The Dossier overview for the user's compliance profile (vouch.dossier.overview): readiness
    from accepted verdicts only, the 12 control cards, the evidence quotes, the next step; and
    each control's requirements, for its working paper (#41); its ISA text in the profile's
    edition (#199), an unreadable edition a plain 500. Redacted as the stream's events are."""
    controls, latest, counted = _standing(profile, memory, saver, OVERVIEW_FAILED)
    discarded = items.discarded(memory.user_id, memory.profile_id, counted)
    clean, _ = redact_data(overview(profile, controls, latest, discarded))
    return clean | {"requirements": paper_requirements(profile, controls)}


def _report(
    profile: ComplianceProfile,
    memory: Memory,
    saver: BaseCheckpointSaver,
    items: remediation.RemediationItems,
    trail: audit.Page,
    failed: str,
) -> dict[str, Any]:
    """The readiness report for the profile with the given trail, redacted (vouch.report): an
    unreadable ISA edition is the plain 500 with `failed`."""
    controls, latest, accepted = _standing(profile, memory, saver, failed)
    decided = items.decided(memory.user_id, memory.profile_id, accepted)
    built = report.readiness_report(
        profile, controls, latest, decided=decided, trail=trail, today=report.today()
    )
    clean, _ = redact_data(built)
    return clean


@router.get("/report")
def readiness_report(
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    items: RemediationItems,
    trail: AuditTrail,
) -> dict[str, Any]:
    """The readiness report for the user's compliance profile (vouch.report, #234), in its
    answer language: readiness with the overview's accepted counts (its remediation count is of
    the approved items the report lists), each control's reviewed verdict, the
    approved remediation items, the not-applicable list, the newest page of the audit trail
    (`trail.next` reads on at GET /api/audit-trail) and the disclaimer, with the report's words.
    No model runs. Dated the server's date in UTC; an unreadable ISA edition is a plain 500.
    The data is redacted as every answer is; the words, the same for every report, are not."""
    page = audit.read(trail, memory.user_id, memory.profile_id)
    built = _report(profile, memory, saver, items, page, REPORT_FAILED)
    return built | {"words": report.words_for(profile.answer_language)}


@router.get("/report/markdown")
def readiness_report_markdown(
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    items: RemediationItems,
    trail: AuditTrail,
) -> Response:
    """The readiness report as one Markdown file (vouch.report.markdown, #57): the same redacted
    report and words as GET /api/report, but with the whole audit trail, newest first, not a
    page. An attachment named for the profile and the day; an unreadable ISA edition is a plain
    500 with EXPORT_FAILED."""
    everything = audit.Page(audit.read_all(trail, memory.user_id, memory.profile_id), None)
    built = _report(profile, memory, saver, items, everything, EXPORT_FAILED)
    name = f"readiness-report-{profile.id}-{built['head']['date']}.md"
    return Response(
        report.markdown(built, report.words_for(profile.answer_language)),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


class ChatRequest(BaseModel):
    # None: a new chat's greeting (#45), the lead asking where they stand (GREETING_REQUEST).
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    page: Page
    control_id: str | None = None  # the control whose working paper is open
    # The chat thread to go on with, from an earlier reply's `thread`; None starts a new one.
    thread_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")] | None = None


def page_context(request: Annotated[ChatRequest, Body()], profile: Profile) -> PageContext:
    """The page the compliance lead is on, its control in the profile's ISA edition (#48); an
    unknown control is a plain 404 and an unreadable edition a plain 500, before the stream
    starts."""
    if request.control_id is None:
        return PageContext(request.page)
    try:
        return PageContext(request.page, profile.isa_control(request.control_id))
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BaseFrameworkError:
        raise _unreadable_edition(profile, CHAT_FAILED) from None


def chat_within_spending(
    context: Annotated[PageContext, Depends(page_context)],
    user: CurrentUser,
    spending: AppSpending,
    chosen: Annotated[str | None, Depends(chosen_model)],
) -> None:
    """The spending guard before a chat message, once the page and the model are known (M3,
    docs/requirements.md 2026-11-03): at the day's limit, the daily limit's 429; a paid model
    with only memory to count in, a 503. A chat message is not counted as an assessment."""
    try:
        spending.check(user.id, paid=chosen is not None)
    except NoSharedCounter as error:
        log.error("chat on OpenRouter needs %s for the daily limit", DATABASE_URL_ENV)
        raise HTTPException(status_code=503, detail=str(error)) from None


@router.post(
    "/chat", response_class=EventSourceResponse, dependencies=[Depends(chat_within_spending)]
)
def chat(
    request: Annotated[ChatRequest, Body()],
    context: Annotated[PageContext, Depends(page_context)],
    profile: Profile,
    models: AppModels,
    chosen: Annotated[str | None, Depends(chosen_model)],
    user: CurrentUser,
    spending: AppSpending,
    memory: UserMemory,
    run: Annotated[AssessmentRun, Depends(assessment_run)],
    running: Running,
) -> Iterable[ServerSentEvent]:
    """The chat agent's reply to the compliance lead's message (vouch.chat_agent), knowing the
    page and control they are on: first a `thread` event, the chat thread's `thread_id` to send
    with the next message; a `token` event per piece of the reply as it is written, a `step`
    per step of an assessment it starts, a `suggestion` (the next step) and an `assessment`
    (one finished) as its tools give them, then `done`; or one `error`, then close. Without a message,
    the greeting: where the lead stands and the next step. Secrets and personal data are
    removed, a guard `step` saying what (security layer 4). The model is the assessments'
    (vouch.model_choice): OpenRouter when a key is set, else the chat's canned fake. Its tokens
    and cost are metered and kept with the assessments' (vouch.spending), whether the reply
    ends in `done` or an `error`: the calls were made. An assessment it starts is counted and
    metered as one from the working paper is."""
    meter = UsageMeter(chosen)
    thread_id = request.thread_id or new_thread_id()

    def record(control_id: str, usage: Usage) -> None:
        record_off_the_event_loop(spending, user.id, control_id, usage)

    yield ServerSentEvent(event="thread", data={"thread_id": thread_id})
    try:
        tools = ChatTools(profile, memory, run, spending, running, record)
        chat = chat_context(
            models.for_chat(chosen, callbacks=[meter]), tools, chat_prompt(profile, context)
        )
        message = request.message or GREETING_REQUEST
        for event, data in chat_turn(chat_graph(run.saver), chat, message, thread_id, memory.scope):
            yield ServerSentEvent(event=event, data=data)
        yield ServerSentEvent(event="done", data={})
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("the chat could not answer")
        yield ServerSentEvent(event="error", data={"message": CHAT_FAILED})
    finally:
        usage = meter.usage()
        log.info(
            "a chat message on %s (%s): %d tokens in, %d tokens out",
            usage.model,
            CHAT_PROMPT_VERSION,
            usage.input_tokens,
            usage.output_tokens,
        )
        record_off_the_event_loop(spending, user.id, CHAT, usage)


DEVELOPERS = [Depends(require_developer)]
"""The developer page's API: a compliance lead gets a 403 (the role itself is never sent)."""


class ModelRequest(BaseModel):
    model: Annotated[str, StringConstraints(strip_whitespace=True, pattern=f"^{MODEL_ID.pattern}$")]


class DailyLimitRequest(BaseModel):
    daily_limit: Annotated[int, Field(ge=1, le=MAX_DAILY_LIMIT, strict=True)]


def _developer_page(
    models: ModelChoice, spending: SpendingGuard, model: str | None = None
) -> dict[str, Any]:
    return models.settings(model) | spending.settings()


@router.get("/developer/settings", dependencies=DEVELOPERS)
def developer_settings(
    models: AppModels, spending: AppSpending, settings: AppSettings, user: CurrentUser
) -> dict[str, Any]:
    """The model, where assessments run (and why on the fake), the prompt version, the daily
    limit, every assessment's usage totalled, and the compliance profiles left out of the
    profile switcher because they can't be loaded (#47), each with its problem."""
    broken = user_profiles(settings, user).broken
    return _developer_page(models, spending) | {"broken_profiles": [asdict(b) for b in broken]}


@router.put("/developer/daily-limit", dependencies=DEVELOPERS)
def change_daily_limit(
    request: DailyLimitRequest, models: AppModels, spending: AppSpending
) -> dict[str, Any]:
    """Each user may start `daily_limit` assessments a day, from the next one on."""
    spending.set_daily_limit(request.daily_limit)
    return _developer_page(models, spending)


@router.put("/developer/model", dependencies=DEVELOPERS)
def change_model(request: ModelRequest, models: AppModels, spending: AppSpending) -> dict[str, Any]:
    """The next assessment runs on `model`, once OpenRouter's model list has it."""
    try:
        model = models.change(request.model)
    except NoKey:
        raise HTTPException(status_code=409, detail=NO_KEY) from None
    except UnknownModel:
        detail = UNKNOWN_MODEL.format(model=request.model)
        raise HTTPException(status_code=422, detail=detail) from None
    except CatalogueUnavailable:
        raise HTTPException(status_code=503, detail=CATALOGUE_UNAVAILABLE) from None
    return _developer_page(models, spending, model)


@router.get("/access")
def access() -> dict[str, str]:
    """The page asks this first after sign-in (#152): reaching here, past require_access, the
    user may use Vouch; a 403 carries the reason to show."""
    return {"access": "allowed"}


@router.get("/{path:path}", include_in_schema=False)
def unknown_api_path(path: str) -> None:
    """Under /api, a path that isn't a route is a 404, never the front end's page."""
    raise HTTPException(status_code=404)


def _check_profiles(settings: Settings) -> None:
    """Stops the app, naming the setting, when the profiles folder can't serve: it isn't a
    folder, or the default profile in it doesn't load."""
    folder = settings.profiles_dir
    if not folder.is_dir():
        raise SystemExit(
            f"Vouch cannot start: {PROFILES_DIR_ENV} names no folder ({folder}). Point it at a "
            "folder of compliance profiles, or unset it for the repository's fixtures/."
        )
    default = scan_profiles(folder).find(DEFAULT_PROFILE)
    if not isinstance(default, ComplianceProfile):
        problem = default.problem if default else "it isn't there"
        raise SystemExit(
            f"Vouch cannot start: the default compliance profile {DEFAULT_PROFILE} in "
            f"{PROFILES_DIR_ENV} ({folder}) can't be loaded: {problem}."
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """The API under /api; given the built front end (`settings.frontend`), that on every other path.

    `settings` defaults to the environment's (load_settings). Settings that can't serve stop
    the app with a message naming the setting.

    A path that is neither a route nor a built file gets the front end's index.html when a
    browser asks for a page, so a deep link survives a refresh (client routing).
    """
    if settings is None:
        try:
            settings = load_settings()
        except SettingsError as error:
            raise SystemExit(f"Vouch cannot start: {error}.") from None
    _check_profiles(settings)
    clerk_mode = settings.auth_mode == "clerk"
    auth = None
    if clerk_mode:
        if settings.clerk is None:  # load_settings never makes these Settings
            raise SystemExit("Vouch cannot start: clerk mode needs the Clerk settings.")
        try:
            auth = ClerkAuth(settings.clerk)
        except SettingsError as error:
            raise SystemExit(f"Vouch cannot start: {error}.") from None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if app.state.auth is not None:
            app.state.auth.close()
        if app.state.database is not None:
            app.state.database.close()
        app.state.models.close()

    # In clerk mode the API's own map (/docs, /openapi.json) isn't served: it'd map it for anyone.
    no_map: dict[str, Any] = {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Vouch", lifespan=lifespan, **(no_map if clerk_mode else {}))
    app.add_exception_handler(DatabaseUnavailable, database_unavailable)
    app.add_exception_handler(_Refused, refused)
    app.add_exception_handler(DailyLimitReached, daily_limit_reached)
    app.add_middleware(MessageLimit)  # security layer 1: no oversized message reaches a route
    remember_secrets(settings)  # security layer 4: its own secret values never leave it
    app.state.settings = settings
    app.state.rate_limit = RateLimit(requests=settings.rate_requests)
    app.state.auth = auth
    app.state.database = Database(settings.database_url) if settings.database_url else None
    app.state.models = ModelChoice(settings, model_store(app.state.database))
    app.state.spending = SpendingGuard(spending_store(app.state.database))
    app.state.running = RunningAssessments()
    frontend = settings.frontend
    app.include_router(public)
    app.include_router(router)
    if frontend is not None:
        if not (frontend / "index.html").is_file():
            raise SystemExit(
                f"Vouch cannot start: no front-end build at {frontend} ({FRONTEND_ENV}). "
                "Build it with `npm run build` in frontend/, or unset "
                f"{FRONTEND_ENV} to run the API alone."
            )
        app.frontend("/", directory=frontend)
    return app


app = create_app()
install_log_redaction()  # security layer 4, once: by import time uvicorn's handlers exist
