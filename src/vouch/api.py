"""The HTTP API: health, one control's assessment streamed as server-sent events up to the
compliance lead's review, the review that resumes it, the user's assessments, the Dossier
overview, and the developer
page's settings.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`. With VOUCH_FRONTEND naming the
built front end (frontend/dist), it serves that too, on one origin, as the container does.
"""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from functools import cache
from importlib.metadata import version
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field, StringConstraints

from vouch.allow_list import GUARD, ToolAllowList, allowed_tools
from vouch.auth import ClerkAuth, ClerkUnavailable, Role, SignedOut
from vouch.database import Connection, Database, DatabaseUnavailable
from vouch.dossier import overview
from vouch.graph import (
    Assessment,
    InvalidReviewError,
    NotWaitingForReviewError,
    RequirementVerdict,
    ReviewDecision,
    UnknownAssessmentError,
    assess_steps,
    load_assessment,
    new_thread_id,
    resume,
    state_serializer,
)
from vouch.isa import IsaControl, UnknownControlError, load_base_framework, load_isa_control
from vouch.limits import TOO_MANY_REQUESTS, MessageLimit, RateLimit
from vouch.memory import Memory, newest_by_control, process_checkpointer, process_store
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
    ComplianceProfile,
    ProfileError,
    UnknownProfileError,
    load_named_profile,
)
from vouch.redaction import install_log_redaction, redact_data, remember_secrets, removal_step
from vouch.settings import (
    DATABASE_URL_ENV,
    DEFAULT_USER,
    FRONTEND_ENV,
    Settings,
    SettingsError,
    User,
    load_settings,
)
from vouch.spending import (
    MAX_DAILY_LIMIT,
    DailyLimitReached,
    NoSharedCounter,
    SpendingGuard,
    Usage,
    UsageMeter,
    spending_store,
)

log = logging.getLogger(__name__)

FAILED = "The assessment could not be completed. Please try again later."
SIGNED_OUT = "Sign in to use Vouch."
CLERK_UNAVAILABLE = "Vouch cannot check your sign-in just now. Please try again shortly."
DEVELOPERS_ONLY = "This is available to developers only."
DATABASE_UNAVAILABLE = "The database is unavailable just now. Please try again shortly."
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


def requested_assessment(request: Annotated[AssessmentRequest, Body()]) -> RequestedAssessment:
    """The control is looked up before the stream starts, so an unknown one is a plain 404."""
    try:
        return RequestedAssessment(load_isa_control(request.control_id), request.note)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@cache
def load_default_profile() -> ComplianceProfile:
    """The default compliance profile, read once; a failed read isn't kept, so it's tried again."""
    return load_named_profile(DEFAULT_PROFILE)


def compliance_profile() -> ComplianceProfile:
    """Loaded before the stream starts, so a broken profile is a plain 500, not an event."""
    try:
        return load_default_profile()
    except (ProfileError, UnknownProfileError):
        log.exception("the compliance profile %s could not be loaded", DEFAULT_PROFILE)
        raise HTTPException(status_code=500, detail=FAILED) from None


Profile = Annotated[ComplianceProfile, Depends(compliance_profile)]


def profile_tools(profile: Profile) -> frozenset[str]:
    """The tools the API lets the model call: the compliance profile's allow-list (security
    layer 2, vouch.allow_list), the one the graph's own guard checks too."""
    return allowed_tools(profile)


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


def user_role(request: Request, user: CurrentUser, settings: AppSettings) -> Role:
    """The user's role, for a route that needs it. Local mode's one user is a developer: local
    mode is for a developer's machine. In clerk mode Clerk's API is asked (cached, auth.py);
    when it can't answer, a 503, never access."""
    if settings.auth_mode == "local":
        return "developer"
    auth: ClerkAuth = request.app.state.auth
    try:
        return auth.role(user)
    except SignedOut:
        raise _signed_out() from None
    except ClerkUnavailable:
        raise HTTPException(status_code=503, detail=CLERK_UNAVAILABLE) from None


def require_developer(role: Annotated[Role, Depends(user_role)]) -> None:
    """A route only a developer may use (the developer page, #34); anyone else gets a 403."""
    if role != "developer":
        raise HTTPException(status_code=403, detail=DEVELOPERS_ONLY)


def rate_limit(request: Request, user: CurrentUser) -> None:
    """Security layer 1: a user over the request rate gets a 429 with a Retry-After."""
    wait = request.app.state.rate_limit.wait_for(user.id)
    if wait is not None:
        headers = {"Retry-After": str(wait)}
        raise HTTPException(status_code=429, detail=TOO_MANY_REQUESTS, headers=headers)


def user_memory(db: AppDatabase, user: CurrentUser, profile: Profile) -> Iterator[Memory]:
    """The user's long-term memory under the compliance profile (#38): Postgres's store with a
    database URL, else this process's. A failure of the database is a 503."""
    if db is None:
        yield Memory(process_store(), user.id, profile.id)
        return
    with db.store() as store:
        yield Memory(store, user.id, profile.id)


UserMemory = Annotated[Memory, Depends(user_memory)]


public = APIRouter(prefix="/api")
"""The routes open to anyone: health alone."""

router = APIRouter(prefix="/api", dependencies=[Depends(current_user), Depends(rate_limit)])
"""Every other route: a route added here needs a signed-in user in clerk mode, within the rate."""


@public.get("/health")
def health(settings: AppSettings, db: AppDatabase) -> dict[str, Any]:
    """Also tells the front end how to sign in (Clerk's publishable key is public), and, with a
    database URL set, whether the database is reachable, set up by vouch-db-setup (#123).
    `status` is liveness: ok while the app answers, whatever the database says."""
    publishable_key = settings.clerk.publishable_key if settings.clerk else None
    auth = {"mode": settings.auth_mode, "publishable_key": publishable_key}
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


def within_daily_limit(
    user: CurrentUser,
    spending: AppSpending,
    requested: Annotated[RequestedAssessment, Depends(requested_assessment)],
    profile: Profile,
    chosen: Annotated[str | None, Depends(chosen_model)],
) -> None:
    """Counts the assessment against the user's daily limit before the stream starts, and only
    once the control, the compliance profile and the model are known (new_assessment calls it,
    and FastAPI skips a dependency whose own dependencies failed, so a 422, a 404, a broken
    profile's 500 or the model choice's 503 takes no count). Over the limit, a 429
    saying when it resets (daily_limit_reached); the counter unavailable, or only in memory for
    a paid model, a 503."""
    try:
        spending.start(user.id, paid=chosen is not None)
    except NoSharedCounter as error:
        log.error("assessments on OpenRouter need %s for the daily limit", DATABASE_URL_ENV)
        raise HTTPException(status_code=503, detail=str(error)) from None


def new_assessment(
    requested: Annotated[RequestedAssessment, Depends(requested_assessment)],
    profile: Profile,
    chosen: Annotated[str | None, Depends(chosen_model)],
    user: CurrentUser,
    spending: AppSpending,
    memory: UserMemory,
) -> str:
    """A new thread for the assessment, before the stream starts: remembered for the user (#38),
    then counted against the daily limit (within_daily_limit). A write that fails is a 503 with
    nothing counted; a count refused removes what was remembered. No model runs either way."""
    thread_id = new_thread_id()
    memory.remember_assessment(thread_id, requested.control.id)
    try:
        within_daily_limit(user, spending, requested, profile, chosen)
    except Exception:
        try:
            memory.forget_assessment(thread_id)
        except Exception:  # left in the index, it's never listed: its run never started
            log.exception("a refused assessment stays in the index")
        raise
    return thread_id


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
    tools: Annotated[frozenset[str], Depends(profile_tools)],
    saver: Checkpointer,
    models: AppModels,
    chosen: Annotated[str | None, Depends(chosen_model)],
    # Remembered and counted only once the control, the profile and the model passed
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
    meter = UsageMeter(chosen)
    try:
        assessed = models.for_assessment(chosen, callbacks=[meter])
        model = ToolAllowList(assessed, tools)
        # assess_steps takes a BaseChatModel; ToolAllowList offers the part the graph uses.
        steps = assess_steps(
            control,
            profile,
            cast(BaseChatModel, model),
            saver,
            note=requested.note,
            thread_id=thread_id,
            scope=memory.scope,
        )
        for item in steps:
            if isinstance(item, Assessment):
                result = {"assessment": item.to_dict(), "usage": meter.usage().to_dict()}
                yield from _redacted("result", result)
            elif isinstance(item, RequirementVerdict):
                yield from _redacted("requirement", {"requirement": asdict(item)})
            else:
                yield from _redacted("step", item.to_dict())
            for refusal in model.take_refusals():
                yield from _redacted("step", {"node": GUARD, "text": refusal})
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("assessment of %s failed", control.id)
        yield ServerSentEvent(event="error", data={"message": FAILED})
    finally:
        record_off_the_event_loop(spending, user.id, control.id, meter.usage())


_REMOVED_FROM = {"result": "assessment", "requirement": "requirement", "step": "step"}
"""What a guard step calls the event something was removed from."""


def _redacted(event: str, data: dict[str, Any]) -> Iterator[ServerSentEvent]:
    """The event with secrets and personal data removed (security layer 4); a guard step before
    it says what was removed."""
    clean, removed = redact_data(data)
    if removed:
        text = removal_step(removed, _REMOVED_FROM[event])
        yield ServerSentEvent(event="step", data={"node": GUARD, "text": text})
    yield ServerSentEvent(event=event, data=clean)


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
def open_assessment(thread_id: str, memory: UserMemory, saver: Checkpointer) -> dict[str, Any]:
    """One of the user's assessments, proposed or reviewed, redacted as the stream's events are.
    404 for an unknown thread, another user's included."""
    try:
        assessment = load_assessment(thread_id, saver, scope=memory.scope)
    except UnknownAssessmentError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    clean, _ = redact_data({"assessment": assessment.to_dict()})
    return clean


@router.post("/assessments/{thread_id}/review")
def review_assessment(
    thread_id: str,
    decision: Annotated[ReviewDecision, Body()],
    saver: Checkpointer,
    memory: UserMemory,
) -> dict[str, Any]:
    """Resume a waiting assessment with the lead's decision; the reviewed Assessment, redacted
    as the stream's events are (its state holds the model's words as written). No model runs.

    404 for an unknown thread, another user's included; 409 for one not waiting (reviewed
    already); 422 for an override naming a requirement the assessment didn't judge, or naming
    one twice (the decision's shape is checked before, also 422)."""
    try:
        reviewed = resume(thread_id, decision, saver, scope=memory.scope)
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
    clean, _ = redact_data({"assessment": reviewed.to_dict()})
    return clean


@router.get("/overview")
def dossier_overview(profile: Profile, memory: UserMemory, saver: Checkpointer) -> dict[str, Any]:
    """The Dossier overview for the user's compliance profile (vouch.dossier.overview): readiness
    from accepted verdicts only, the 12 control cards, the evidence quotes, the next step. Its
    quotes are redacted as the stream's events are."""
    latest = newest_by_control(memory, saver)  # from each control's newest thread
    clean, _ = redact_data(overview(profile, load_base_framework().controls, latest))
    return clean


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
def developer_settings(models: AppModels, spending: AppSpending) -> dict[str, Any]:
    """The model, where assessments run (and why on the fake), the prompt version, the daily
    limit, and every assessment's usage totalled."""
    return _developer_page(models, spending)


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


@router.get("/{path:path}", include_in_schema=False)
def unknown_api_path(path: str) -> None:
    """Under /api, a path that isn't a route is a 404, never the front end's page."""
    raise HTTPException(status_code=404)


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
    app.add_exception_handler(DailyLimitReached, daily_limit_reached)
    app.add_middleware(MessageLimit)  # security layer 1: no oversized message reaches a route
    remember_secrets(settings)  # security layer 4: its own secret values never leave it
    app.state.settings = settings
    app.state.rate_limit = RateLimit()
    app.state.auth = auth
    app.state.database = Database(settings.database_url) if settings.database_url else None
    app.state.models = ModelChoice(settings, model_store(app.state.database))
    app.state.spending = SpendingGuard(spending_store(app.state.database))
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
