"""The HTTP API: health, one control's assessment streamed as server-sent events up to the
compliance lead's review, a profile pass over every applicable control not yet accepted (#371),
the review that resumes an assessment, the lead's remediation decisions, the user's assessments,
the Dossier overview, and the developer page's settings.

Run it locally with `uv run uvicorn vouch.api:app --port 8000`. With VOUCH_FRONTEND naming the
built front end (frontend/dist), it serves that too, on one origin, as the container does.
"""

import asyncio
import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import Annotated, Any, NamedTuple
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field, StringConstraints

from vouch import (
    audit,
    company_profiles,
    documents_tab,
    goal,
    plan,
    questions,
    remediation,
    report,
    report_pdf,
    rounds,
    tracing,
)
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
from vouch.citations import LeadStatement
from vouch.company_page import company_facts
from vouch.current_company import CurrentCompanyStore, current_company_store, current_of
from vouch.database import Connection, Database, DatabaseUnavailable, Goal
from vouch.documents import library_summaries
from vouch.dossier import (
    Applicability,
    PaperRequirement,
    applicability,
    applicable_controls,
    not_applicable_refusal,
    overview,
    requirements_by_control,
)
from vouch.graph import (
    PROMPT_VERSION,
    Assessment,
    ConfirmFindings,
    InvalidReviewError,
    NotWaitingForReviewError,
    ReviewDecision,
    UnknownAssessmentError,
    assessment_loader,
    load_assessment,
    new_thread_id,
    state_serializer,
)
from vouch.isa import (
    BaseFrameworkError,
    IsaControl,
    UnknownControlError,
    load_base_framework,
)
from vouch.language import lead_statement_label, stale_reason, statement_note
from vouch.limits import TOO_MANY_REQUESTS, MessageLimit, RateLimit
from vouch.memory import (
    Entry,
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
from vouch.paths import SAMPLE_LIBRARY_DIR
from vouch.profile import (
    DEFAULT_PROFILE,
    SAMPLE_LIBRARY,
    BrokenProfile,
    ComplianceProfile,
    ProfileError,
    ProfileScan,
    load_problem,
    profile_folder_names,
    protection_scope,
    scan_profiles,
)
from vouch.profile_pass import (
    FALLBACK,
    PROFILE_PASS_FAILED,
    PROFILE_PASS_PROMPT_VERSION,
    ProfilePassRefused,
    run_profile_pass,
)
from vouch.prompts import prompt_previews
from vouch.redaction import (
    configure_logging,
    redact,
    redact_data,
    redacted_event,
    remember_secrets,
)
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
    PROFILE_PASS,
    DailyLimitReached,
    NoSharedCounter,
    ProfilePassLimitReached,
    SpendingGuard,
    Usage,
    UsageMeter,
    count_assessment,
    spending_store,
)
from vouch.tracing import TraceFacts, announce_tracing, run_traced

log = logging.getLogger(__name__)

SIGNED_OUT = "Sign in to use Vouch."
CLERK_UNAVAILABLE = "Vouch cannot check your sign-in just now. Please try again shortly."
DEVELOPERS_ONLY = "This is available to developers only."
PROTECTION_LEVELS_FAILED = "What each protection level brings into scope could not be read."
CONTROLS_FAILED = "The framework's controls could not be read."
PROMPTS_FAILED = "The prompts could not be built."
NOT_A_PROMPT_CONTROL = "{control} is not a control the current company applies."
NO_PROMPT_CONTROL = "The current company applies no control."
SAMPLE_LIBRARY_FAILED = "The sample library could not be read."
DOCUMENTS_FAILED = "The documents could not be read."
COMPANY_FAILED = "The company could not be loaded."
# The library's folders a company's evidence chooses from (company_profiles.Evidence's fields),
# in the order step 6 lists them.
LIBRARY_SOURCES = ("policies", "records")
NOT_A_MEMBER = (
    "Vouch is open to members of the course's Discord server. This Discord account is not in it."
)
SIGN_IN_AGAIN = "Please sign in with Discord again, so Vouch can check your Discord servers."
DATABASE_UNAVAILABLE = "The database is unavailable just now. Please try again shortly."
UNKNOWN_PROFILE = "There is no such compliance profile."
NOT_A_MADE_COMPANY = "Only a company made in company setup can be removed."
OVERVIEW_FAILED = "The overview could not be loaded just now. Please try again."
REPORT_FAILED = "The readiness report could not be loaded just now. Please try again."
EXPORT_FAILED = "The readiness report could not be exported just now. Please try again."
QUESTION_FAILED = "The question could not be logged just now. Please try again."
MAX_QUESTION_ID = 2**63 - 1
"""The highest open question id: the ceiling of vouch_open_questions' bigserial `id`. A larger
one in the path is a 422, never sent to Postgres (whose refusal would be a 503)."""
RUNNING_NO_FRESH_START = "An assessment is running: start afresh once it has finished."
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
"""Names the compliance profile a request is for (#47): the app sets it on every call to the
current company it opened under (#480). Without it, the current company."""


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


def request_refused(request: Request, error: Exception) -> Response:
    """FastAPI's own 422 for a request its checks refuse, written as ASCII JSON: the input it
    echoes may hold a lone surrogate, which no UTF-8 body can carry (#385)."""
    assert isinstance(error, RequestValidationError)
    body = json.dumps({"detail": jsonable_encoder(error.errors())}, ensure_ascii=True)
    return Response(body, status_code=422, media_type="application/json")


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


def app_company_profiles(db: AppDatabase) -> company_profiles.CompanyProfiles:
    """Where companies made in the app are kept (#385): Postgres with a database URL, else this
    process."""
    if db is None:
        return company_profiles.process_company_profiles()
    return company_profiles.PostgresCompanyProfiles(db)


CompanyProfiles = Annotated[company_profiles.CompanyProfiles, Depends(app_company_profiles)]


def app_current_company(request: Request) -> CurrentCompanyStore:
    """Where the current company is kept (vouch.current_company, #480)."""
    return request.app.state.current_company


AppCurrentCompany = Annotated[CurrentCompanyStore, Depends(app_current_company)]


def stored_current_company(store: CurrentCompanyStore) -> str | None:
    """The current company's stored id; None when nothing is stored, or when it can't be read
    (logged): then the default profile is current, as the file profiles are listed when the
    stored companies can't be read."""
    try:
        return store.get()
    except DatabaseUnavailable:
        log.warning("the current company can't be read: the default profile is current")
        return None


def file_profiles(settings: Settings) -> ProfileScan:
    """Every profile in the profiles folder, each read once, except the sample library's
    reference profile, which is no company (#370)."""
    scan = scan_profiles(settings.profiles_dir)
    return ProfileScan(
        tuple(p for p in scan.loaded if p.id != SAMPLE_LIBRARY),
        tuple(p for p in scan.broken if p.id != SAMPLE_LIBRARY),
    )


SHADOWED = "its id is now a file profile's"
"""Why a company made in the app is left out when a profile folder of its id was added since."""


def user_profiles(settings: Settings, user: User, companies: CompanyProfiles) -> ProfileScan:
    """The compliance profiles a user is shown (#47), GET /api/profiles' and the developer
    page's: for now, for every user, the profiles folder's (file_profiles), then the companies
    made in the app, oldest first (#385), each read once. A broken one is left out (and listed
    for the developer page), never a failure; so is a stored one whose id a file profile has
    taken since, as the file profile is the one opened. When the stored ones can't be read, the
    file ones (logged). Which profile a request is under is compliance_profile's to decide,
    with the same file-first rule; a per-user choice must change both (#163)."""
    return _profile_scan(settings, _stored_companies(companies))


def _stored_companies(companies: CompanyProfiles) -> list[company_profiles.StoredProfile] | None:
    """Every live company made in the app, oldest first; None when they can't be read (logged)."""
    try:
        return companies.all()
    except DatabaseUnavailable:
        log.warning("the companies made in the app can't be read: listing the file profiles only")
        return None


def _profile_scan(
    settings: Settings, rows: list[company_profiles.StoredProfile] | None
) -> ProfileScan:
    files = file_profiles(settings)
    if rows is None:
        return files
    file_ids = {p.id for p in files.loaded} | {p.id for p in files.broken}
    stored: list[ComplianceProfile | BrokenProfile] = [
        BrokenProfile(row.id, SHADOWED)
        if row.id in file_ids
        else company_profiles.read_stored_once(row)
        for row in rows
    ]
    return ProfileScan(
        (*files.loaded, *(p for p in stored if isinstance(p, ComplianceProfile))),
        (*files.broken, *(p for p in stored if isinstance(p, BrokenProfile))),
    )


def compliance_profile(
    settings: AppSettings,
    user: CurrentUser,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
    chosen: Annotated[str | None, Header(alias=PROFILE_HEADER)] = None,
) -> ComplianceProfile:
    """The profile the request names, else the current company (#480, _current_profile): a file
    profile first, so a request under one never reads the companies made in the app, else a
    company made in the app (the file-first rule user_profiles lists by). Settled before the
    stream starts: one there is none of is a 404; a broken one a plain 500; a stored company's
    that can't be read a 503."""
    if chosen is None:
        return _current_profile(settings, companies, current)
    name = chosen
    found = _find_profile(settings, companies, name)
    if found is None:
        raise HTTPException(status_code=404, detail=UNKNOWN_PROFILE)
    if isinstance(found, BrokenProfile):
        log.error("the compliance profile %s can't be used: %s", name, found.problem)
        raise HTTPException(status_code=500, detail=ASSESSMENT_FAILED)
    return found


def _find_profile(
    settings: Settings, companies: company_profiles.CompanyProfiles, name: str
) -> ComplianceProfile | BrokenProfile | None:
    """The profile `name`: a file profile first, else a live company made in the app."""
    found = file_profiles(settings).find(name)
    if found is None and company_profiles.is_profile_name(name):
        row = companies.one(name)
        found = None if row is None else company_profiles.read_stored_once(row)
    return found


def _current_profile(
    settings: Settings, companies: company_profiles.CompanyProfiles, current: CurrentCompanyStore
) -> ComplianceProfile:
    """The current company's profile, by current_of's one rule, as GET /api/profiles lists it:
    the stored company while it is usable, else Nordlicht, else the first listed. A stored
    company made in the app that can't be read (the database down) falls back the same way,
    logged, as the list leaves it out then. The fallback is found among the file profiles first,
    so a request under one reads no company made in the app; the companies are read only when
    no file profile is listed."""
    stored = stored_current_company(current)
    if stored is not None:
        try:
            found = _find_profile(settings, companies, stored)
        except DatabaseUnavailable:
            log.warning(
                "the current company can't be read (%s, made in the app): falling back", stored
            )
            found = None
        if isinstance(found, ComplianceProfile):
            return found
    files = file_profiles(settings)  # first in every list, so the fallback is one of them if any
    scan = files if files.loaded else _profile_scan(settings, _stored_companies(companies))
    name = current_of(scan, None)
    profile = None if name is None else scan.find(name)
    if not isinstance(profile, ComplianceProfile):
        raise HTTPException(status_code=404, detail=UNKNOWN_PROFILE)
    return profile


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
    an event. A control not applicable as a whole under the profile is a 409 (#454): nothing
    could review its result. Each is refused before the run is held or counted."""
    try:
        control = profile.isa_control(request.control_id)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BaseFrameworkError:
        raise _unreadable_edition(profile, ASSESSMENT_FAILED) from None
    if applicability(profile, [control]).reasons:
        raise HTTPException(status_code=409, detail=not_applicable_refusal(control.id))
    return RequestedAssessment(control, request.note)


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


def remediation_plan(db: AppDatabase) -> plan.RemediationPlan:
    """Where the improvement plan's marks are kept, with their audit entries (#410): Postgres
    with a database URL, else this process."""
    return plan.process_plan() if db is None else plan.PostgresPlan(db)


RemediationPlan = Annotated[plan.RemediationPlan, Depends(remediation_plan)]


def profile_goals(db: AppDatabase) -> goal.ProfileGoals:
    """Where each user's Goal under each profile is kept, with its audit entries (#369):
    Postgres with a database URL, else this process."""
    return goal.process_goals() if db is None else goal.PostgresGoals(db)


ProfileGoals = Annotated[goal.ProfileGoals, Depends(profile_goals)]


def review_rounds(db: AppDatabase) -> rounds.ReviewRounds:
    """Where each user's review rounds under each profile are kept, with their audit entries
    (#402): Postgres with a database URL, else this process."""
    return rounds.process_rounds() if db is None else rounds.PostgresRounds(db)


ReviewRounds = Annotated[rounds.ReviewRounds, Depends(review_rounds)]


def open_questions(db: AppDatabase) -> questions.OpenQuestions:
    """Where each user's open questions under each profile are kept, with their audit entries
    (#414): Postgres with a database URL, else this process."""
    return questions.process_questions() if db is None else questions.PostgresQuestions(db)


OpenQuestions = Annotated[questions.OpenQuestions, Depends(open_questions)]


def lead_statements(asked: OpenQuestions, memory: UserMemory) -> tuple[LeadStatement, ...]:
    """The lead's statements under the profile (#422): their answered questions asked since the
    fresh start, on any control. Questions that can't be read leave none, logged: a missing
    statement can only lower a verdict, never raise it."""
    try:
        listed = asked.listed(memory.user_id, memory.profile_id)
        return tuple(questions.lead_statements(questions.since(listed, memory.fresh_start())))
    except DatabaseUnavailable:
        log.warning("the open questions can't be read: assessing without the lead's statements")
        return ()


LeadStatements = Annotated[tuple[LeadStatement, ...], Depends(lead_statements)]


def _evidence_in_answer_language(served: dict[str, Any], language: str) -> dict[str, Any]:
    """The served assessment with its evidence's freshness in the answer language: `stale_word`,
    the report's stale label (as the Documents tab's, so a page and the report flag alike), and
    each stale citation's reason as the cap's note says it (vouch.language.stale_reason), for the
    stale flag (#359); and `current_word`, the Documents tab's word for a document that isn't
    stale, which the evidence column shows in its place (#484); and `statement_note`, what a lead
    statement's cap asks for (#439), with `statement_word`, the evidence column's word for a
    requirement a lead's statement shows: its citation's label (#498). The stored assessment keeps
    English's reason."""
    for requirement in served["requirements"]:
        for citation in requirement["citations"]:
            if citation["stale"]:
                citation["stale_because"] = stale_reason(citation["stale_because"], language)
    _, document_words = documents_tab.document_words(language)
    return served | {
        "stale_word": report.words_for(language)["labels"]["stale"],
        "current_word": document_words["current"],
        "statement_note": statement_note(language),
        "statement_word": lead_statement_label(language),
    }


def assessment_data(
    assessment: Assessment,
    decided: dict[remediation.Key, remediation.DecidedItem] | None = None,
    marks: dict[remediation.Key, remediation.Mark] | None = None,
) -> dict[str, Any]:
    """The assessment as the API sends it, each remediation item with its status (#43): a draft,
    or as the lead decided it (`decided`, none for a run just made, its findings just confirmed,
    or just reviewed: nothing is decided before the findings are), and `review_step`, the step
    of the review it waits at (#372; null once reviewed), and `before_three_step_review`, true
    for one reviewed before the three-step review (#373): it still counts as reviewed, and
    nothing stored is rewritten. Each item carries its priority and done mark in the improvement
    plan (`marks`, #410), in both goals. Its stale citations are flagged in its answer
    language (#359): `stale_word`, `current_word` (#484) and each reason
    (`_evidence_in_answer_language`)."""
    review = assessment.review
    served = _evidence_in_answer_language(assessment.to_dict(), assessment.answer_language)
    return served | {
        "remediation": remediation.shown_items(assessment, decided or {}, marks),
        "review_step": remediation.review_step(assessment, decided or {}),
        "before_three_step_review": review is not None and not review.three_step,
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
    statements: LeadStatements,
) -> AssessmentRun:
    """What the request's assessment runs on (vouch.runs.AssessmentRun), metered afresh, with
    the lead's statements (#422)."""
    meter = UsageMeter(chosen)
    return AssessmentRun(models, chosen, tools, saver, meter, memory.scope, statements)


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
    """The daily limit's 429: `limit: "daily"` tells it apart from the request rate's, and
    `"profile-pass"` the profile passes' own limit (#371)."""
    assert isinstance(error, DailyLimitReached)
    content = {
        "detail": error.message(),
        "limit": "profile-pass" if isinstance(error, ProfilePassLimitReached) else "daily",
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
    by), then close; or one `error`, then close. The `result` waits for the run's trace to be
    sent, at most vouch.tracing's bound (#541), as a client may stop reading at it. Each
    request is a new assessment; with a
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
    is redacted (security layer 4). With the Langfuse keys set, the run is one trace
    (vouch.tracing, #460).
    """
    control = requested.control
    facts = TraceFacts(
        tracing.ASSESSMENT,
        user.id,
        profile.id,
        run.chosen,
        PROMPT_VERSION,
        control_id=control.id,
        thread_id=thread_id,
    )
    # The `result` is held until the run's trace is sent (#541): a client may stop reading at
    # `result` (the browser does), and a send after it would run once the response is over.
    held_result: list[ServerSentEvent] = []
    try:
        with run.models.traced(facts) as run_config:
            events = assessment_events(
                control,
                profile,
                run,
                memory,
                thread_id=thread_id,
                note=requested.note,
                run_config=run_config,
            )
            for event, data in events:
                is_result = isinstance(data, Assessment)
                if is_result:
                    usage = run.meter.usage().to_dict()
                    data = {"assessment": assessment_data(data), "usage": usage}
                for name, clean in redacted_event(event, data):
                    sse = ServerSentEvent(event=name, data=clean)
                    if is_result:
                        held_result.append(sse)
                    else:
                        yield sse
        yield from held_result  # the trace sent, within its bound (vouch.tracing.send_trace)
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("assessment of %s failed", control.id)
        yield ServerSentEvent(event="error", data={"message": ASSESSMENT_FAILED})
    finally:
        record_off_the_event_loop(spending, user.id, control.id, run.meter.usage())


@router.post("/profile-pass")
def create_profile_pass(
    profile: Profile,
    chosen: Annotated[str | None, Depends(chosen_model)],
    models: AppModels,
    settings: AppSettings,
    user: CurrentUser,
    spending: AppSpending,
    memory: UserMemory,
    saver: Checkpointer,
    running: Running,
    statements: LeadStatements,
) -> Any:
    """A profile pass (vouch.profile_pass, #371): every applicable control of the user's
    compliance profile not yet accepted, assessed in one guarded model run, each new assessment
    waiting for review. It holds
    the lead's one run, as an assessment does: another of theirs going is a 409.

    200: each new assessment's thread id, control, status and verdict, the controls skipped, each
    `{control_id, reason}` with reason `accepted` or `in-review` (#437), and the pass's usage,
    redacted. With every applicable control accepted or in review, nothing is counted or called.
    A prompt and expected reply estimated over the token threshold (VOUCH_PROFILE_PASS_TOKENS),
    or an allowed source that can't be read, is a 422 with `"fallback": "per-control"`, nothing
    counted. Then the pass is counted against its own daily
    limit (a 429 with `"limit": "profile-pass"` over it; a 503 for a paid model with only memory to
    count in, or a counter that can't be read). A model run that fails, or a reply claiming an
    audit result twice, is a 502 with one sentence and nothing kept; the pass stays counted, as its
    calls were made. A counted pass's usage is recorded once, under PROFILE_PASS, however it
    ends. With the Langfuse keys set, the pass is one trace (vouch.tracing, #460)."""
    if not running.try_hold(memory.scope):
        raise HTTPException(status_code=409, detail=RUNNING_ALREADY)
    meter = UsageMeter(chosen)
    counted = False

    def count() -> None:
        nonlocal counted
        try:
            spending.start_profile_pass(user.id, paid=chosen is not None)
        except NoSharedCounter as error:
            log.error("profile passes on OpenRouter need %s for their limit", DATABASE_URL_ENV)
            raise HTTPException(status_code=503, detail=str(error)) from None
        counted = True

    facts = TraceFacts(
        tracing.PROFILE_PASS, user.id, profile.id, chosen, PROFILE_PASS_PROMPT_VERSION
    )
    try:
        model = models.for_profile_pass(chosen, callbacks=[meter], profile=profile)
        with models.traced(facts) as run_config:
            result = run_traced(
                run_config,
                tracing.PROFILE_PASS,
                lambda: run_profile_pass(
                    profile,
                    memory,
                    saver,
                    model,
                    max_tokens=settings.profile_pass_tokens,
                    count=count,
                    statements=statements,
                ),
            )
    except ProfilePassRefused as refusal:
        return JSONResponse(status_code=422, content={"detail": str(refusal), "fallback": FALLBACK})
    except Exception:
        if not counted:  # refused before any call: the limit's 429, the counter's 503
            raise
        log.exception("a profile pass of %s failed", profile.id)
        raise HTTPException(status_code=502, detail=PROFILE_PASS_FAILED) from None
    finally:
        if counted:
            spending.record(user.id, PROFILE_PASS, meter.usage())
        running.release(memory.scope)
    answer = {
        "assessments": [
            {
                "thread_id": a.thread_id,
                "control_id": a.control_id,
                "status": a.status,
                "verdict": a.verdict,
            }
            for a in result.assessments
        ],
        "skipped": [{"control_id": s.control_id, "reason": s.reason} for s in result.skipped],
        "usage": meter.usage().to_dict(),
    }
    clean, _ = redact_data(answer)
    return clean


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
    thread_id: str,
    memory: UserMemory,
    saver: Checkpointer,
    items: RemediationItems,
    marks: RemediationPlan,
) -> dict[str, Any]:
    """One of the user's assessments, proposed or reviewed, redacted as the stream's events are.
    404 for an unknown thread, another user's included."""
    try:
        assessment = load_assessment(thread_id, saver, scope=memory.scope)
    except UnknownAssessmentError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    decided = items.decided(memory.user_id, memory.profile_id, [thread_id])
    marked = marks.marks(memory.user_id, memory.profile_id, [thread_id])
    clean, _ = redact_data({"assessment": assessment_data(assessment, decided, marked)})
    return clean


def _noting_work(memory: Memory) -> None:
    """Note the lead worked now; a step of the review stands whatever happens here."""
    try:
        memory.note_worked()
    except Exception:
        log.exception("a review's time wasn't kept")


@router.post("/assessments/{thread_id}/findings")
def confirm_findings(
    thread_id: str,
    findings: Annotated[ConfirmFindings, Body()],
    trail: AuditTrail,
    memory: UserMemory,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """Confirm findings, the review's first step (#372): the waiting assessment's verdicts
    confirmed, any overridden; the assessment, redacted, now waiting at Plan fixes (or Accept,
    with no remediation item). No model runs. The step and its audit entries are written
    together (vouch.audit): when the entries can't be, nothing is, and it's a 503.

    404 for an unknown thread, another user's included; 409 for one not waiting at Confirm
    findings (confirmed or reviewed already, or another step of its review running); 422 for an
    override naming a requirement the assessment didn't judge, or naming one twice (the
    findings' shape is checked before, also 422)."""
    try:
        confirmed = audit.confirm_findings(
            trail, thread_id, findings, reviewer=audit.Reviewer(user.id, name), scope=memory.scope
        )
    except UnknownAssessmentError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NotWaitingForReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidReviewError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _noting_work(memory)
    clean, _ = redact_data({"assessment": assessment_data(confirmed)})
    return clean


@router.post("/assessments/{thread_id}/review")
def review_assessment(
    thread_id: str,
    decision: Annotated[ReviewDecision, Body()],
    trail: AuditTrail,
    items: RemediationItems,
    marks: RemediationPlan,
    memory: UserMemory,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """Resume a waiting assessment with the lead's decision, Accept (the review's last step,
    #372) or Reject (at any step); the reviewed Assessment, redacted as the stream's events are
    (its state holds the model's words as written). No model runs. The decision and its audit
    entry are written together (vouch.audit): when the entry can't be, nothing is, and it's a
    503.

    404 for an unknown thread, another user's included; 409 for one not waiting (reviewed
    already), an Accept before the findings are confirmed, or with remediation items left
    drafts (`detail` names their requirements); 422 for a decision that doesn't validate, one
    carrying overrides included (they go with Confirm findings)."""

    # Read before the review's transaction, so it holds one connection: a draft decided
    # meanwhile only makes fewer drafts, and the remediation route can't decide one while the
    # review holds the thread's lock.
    decided = items.decided(memory.user_id, memory.profile_id, [thread_id])

    def drafts(waiting: Assessment) -> list[str]:
        return remediation.drafts_left(waiting, decided)

    try:
        reviewed = audit.review(
            trail,
            thread_id,
            decision,
            reviewer=audit.Reviewer(user.id, name),
            scope=memory.scope,
            drafts=drafts,
        )
    except UnknownAssessmentError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NotWaitingForReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _noting_work(memory)
    # Its items as they stand now (#410): read again, as a decision may have landed between the
    # read above and the review taking the thread's lock.
    now_decided = items.decided(memory.user_id, memory.profile_id, [thread_id])
    marked = marks.marks(memory.user_id, memory.profile_id, [thread_id])
    clean, _ = redact_data({"assessment": assessment_data(reviewed, now_decided, marked)})
    return clean


@router.post("/assessments/{thread_id}/remediation/{requirement_id}")
def decide_remediation(
    thread_id: str,
    requirement_id: str,
    request: Annotated[remediation.DecisionRequest, Body()],
    items: RemediationItems,
    marks: RemediationPlan,
    saver: Checkpointer,
    memory: UserMemory,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead approves or discards one of an assessment's remediation items (#43), once its
    findings are confirmed (Plan fixes, #372; an accepted one's too),
    an approval maybe with another owner role or due date: the item as kept, redacted. The
    decision and its audit entry are kept together, under the user and the compliance profile;
    when the entry can't be written, neither is (a 503). The same decision again changes
    nothing and writes no entry.

    404 for an unknown thread (another user's or profile's too) or a requirement with no item;
    409 for an assessment whose findings aren't confirmed, or rejected, an item decided the
    other way, or approved with other
    values (`detail` names the kept ones); 422 for an approval due on a date past everywhere or
    beyond the horizon, or naming no owner where Vouch suggested none, with the hint in `detail`
    (the request's shape is checked before, also 422)."""
    try:
        kept = remediation.decide(
            items,
            thread_id,
            requirement_id,
            request,
            reviewer=audit.Reviewer(user.id, name),
            profile_id=memory.profile_id,
            scope=memory.scope,
        )
    except (UnknownAssessmentError, remediation.UnknownItemError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (remediation.NotDecidableError, remediation.DecidedAlreadyError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (remediation.DueDateError, remediation.OwnerNeededError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # As the assessment shows it, with its priority and done mark (#410).
    assessment = load_assessment(thread_id, saver, scope=memory.scope)
    marked = marks.marks(memory.user_id, memory.profile_id, [thread_id])
    shown = remediation.shown_items(assessment, {(thread_id, requirement_id): kept}, marked)
    item = next(i for i in shown if i["requirement_id"] == requirement_id)
    clean, _ = redact_data({"item": item})
    return clean


def _planned(mark: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """A plan route's answer (#410): the item as marked, redacted; its refusals as HTTP."""
    try:
        item = mark()
    except (UnknownAssessmentError, remediation.UnknownItemError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (plan.NotInImprovementGoal, plan.NotPlannable) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    clean, _ = redact_data({"item": item})
    return clean


@router.put("/assessments/{thread_id}/remediation/{requirement_id}/priority")
def set_remediation_priority(
    thread_id: str,
    requirement_id: str,
    request: Annotated[plan.PriorityRequest, Body()],
    marks: RemediationPlan,
    goals: ProfileGoals,
    memory: UserMemory,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead sets an approved, not done remediation item's priority in the improvement plan
    (#410), with its audit entry, together (when the entry can't be written, neither is: a 503);
    the item as kept. The priority it has already writes nothing.

    404 for an unknown thread (another user's or profile's too) or a requirement with no item;
    409 under Audit readiness, for a draft, discarded or done item, or a rejected assessment;
    422 for any other value or no body."""
    current = goals.goal(memory.user_id, memory.profile_id)
    return _planned(
        lambda: plan.set_priority(
            marks,
            thread_id,
            requirement_id,
            request.priority,
            goal=current,
            reviewer=audit.Reviewer(user.id, name),
            profile_id=memory.profile_id,
            scope=memory.scope,
        )
    )


@router.post("/assessments/{thread_id}/remediation/{requirement_id}/done")
def mark_remediation_done(
    thread_id: str,
    requirement_id: str,
    request: Annotated[plan.DoneRequest, Body()],
    marks: RemediationPlan,
    goals: ProfileGoals,
    memory: UserMemory,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead marks an approved remediation item of an accepted control done, with what was
    done (#410), and its audit entry, together (when the entry can't be written, neither is: a
    503); the item as kept. Done is final: the same note again writes nothing.

    404 for an unknown thread (another user's or profile's too) or a requirement with no item;
    409 under Audit readiness, for a draft or discarded item, an assessment not accepted, or one
    done with another note (`detail` names it); 422 for a blank, longer or missing note."""
    current = goals.goal(memory.user_id, memory.profile_id)
    return _planned(
        lambda: plan.mark_done(
            marks,
            thread_id,
            requirement_id,
            request.note,
            goal=current,
            reviewer=audit.Reviewer(user.id, name),
            profile_id=memory.profile_id,
            scope=memory.scope,
        )
    )


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
    `before` for the page after (null: no more). The notes Vouch wrote (an approval or discard,
    a question, a priority change) are served in the profile's answer language, as the report's
    trail serves them (vouch.report.trail_entries, #535); a lead's own notes as written. The
    trail is append-only: every entry stays stored as written, and no route changes or removes
    one."""
    page = audit.read(trail, user.id, profile.id, limit=limit, before=before)
    entries = report.trail_entries(page.entries, profile.answer_language)
    clean, _ = redact_data({"entries": entries})
    return clean | {"next": page.next}


@router.get("/profiles")
def list_profiles(
    settings: AppSettings,
    user: CurrentUser,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
) -> dict[str, Any]:
    """The compliance profiles listed for the user (#47): each one's id, company, country and
    answer language. A profile that can't be loaded is left out (the developer page names it).
    `default` is the default profile (Nordlicht); null when that one is left out. `current` is
    the current company (#480), the one the app opens under and a request without
    X-Vouch-Profile is under; null with none listed."""
    scan = user_profiles(settings, user, companies)
    listed = [listed_profile(p) for p in scan.loaded]
    default = scan.find(DEFAULT_PROFILE)
    return {
        "profiles": listed,
        "default": default.id if default in scan.loaded else None,
        "current": current_of(scan, stored_current_company(current)),
    }


def listed_profile(profile: ComplianceProfile) -> dict[str, str]:
    """A profile as GET /api/profiles lists it."""
    return {
        "id": profile.id,
        "company": profile.company,
        "country": profile.country,
        "answer_language": profile.answer_language,
    }


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


class Standing(NamedTuple):
    """What the overview, the report, the review rounds and the Documents tab are built from
    (`_standing`): which of the profile's controls apply (#454), each applicable control's
    newest assessment, and the accepted ones' thread ids (whose remediation decisions count)."""

    scope: Applicability
    latest: dict[str, Assessment]
    accepted: list[str]


def _standing(
    profile: ComplianceProfile, memory: Memory, saver: BaseCheckpointSaver, failed: str
) -> Standing:
    """The standing under the profile: its controls in its ISA edition (unreadable: the plain
    500 with `failed`), decided applicable or not once; the newest assessment of each applicable
    one, an assessment of a control not applicable as a whole left out, so nothing reading them
    counts it."""
    try:
        controls = profile.isa_edition().controls
    except BaseFrameworkError:
        raise _unreadable_edition(profile, failed) from None
    scope = applicability(profile, controls)
    latest = scope.counted(newest_by_control(memory, saver))  # from each control's newest thread
    accepted = [a.thread_id for a in latest.values() if a.status == "accepted"]
    return Standing(scope, latest, accepted)


@router.get("/overview")
def dossier_overview(
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    items: RemediationItems,
    marks: RemediationPlan,
    goals: ProfileGoals,
    review: ReviewRounds,
    asked: OpenQuestions,
) -> dict[str, Any]:
    """The Dossier overview for the user's compliance profile (vouch.dossier.overview): readiness
    from accepted verdicts only, the 12 control cards, the evidence quotes, the next step; and
    each control's requirements, for its working paper (#41); its ISA text in the profile's
    edition (#199), an unreadable edition a plain 500; the user's Goal under the profile
    (#369); and their review rounds (#402): the open one, and the comparison with the last one
    closed since the fresh start; in the Internal improvement goal, each card's improvement plan
    flags (#410); the open questions asked since the fresh start, by control id, oldest first,
    and the reasons they suggest a re-assessment for, in both goals (#414). Redacted as the
    stream's events are."""
    scope, latest, counted = _standing(profile, memory, saver, OVERVIEW_FAILED)
    decided = items.decided(memory.user_id, memory.profile_id, counted)
    discarded = {key for key, item in decided.items() if item.status == "discarded"}
    approved = {key for key, item in decided.items() if item.status == "approved"}
    current = goals.goal(memory.user_id, memory.profile_id)
    listed = questions.since(asked.listed(memory.user_id, memory.profile_id), memory.fresh_start())
    at = audit.now()
    flags = plan.with_reasons(
        _plan_flags(current, memory, saver, items, marks), questions.reasons(latest, listed, at)
    )
    shown = dict(overview(profile, scope, latest, discarded, approved, flags))
    clean, _ = redact_data(shown | {"questions": questions.by_control(listed, at)})
    return clean | {
        "requirements": paper_requirements(profile, scope.controls),
        "goal": current,
        "rounds": _rounds(review, memory, saver, scope.applicable, latest),
    }


def _plan_flags(
    current: Goal,
    memory: Memory,
    saver: BaseCheckpointSaver,
    items: remediation.RemediationItems,
    marks: plan.RemediationPlan,
) -> dict[str, plan.CardFlags]:
    """The cards' improvement plan flags (#410): from every assessment listed since the fresh
    start, with the user's decisions and marks on them; none read under Audit readiness."""
    if current != plan.IMPROVEMENT:
        return {}
    return plan.card_flags(*_plan_reads(memory, saver, items, marks), current)


def _plan_reads(
    memory: Memory,
    saver: BaseCheckpointSaver,
    items: remediation.RemediationItems,
    marks: plan.RemediationPlan,
) -> tuple[
    list[Assessment],
    dict[remediation.Key, remediation.DecidedItem],
    dict[remediation.Key, remediation.Mark],
]:
    """What the improvement plan is read from (#410): every assessment listed since the fresh
    start, newest first, with the user's decisions and marks on them."""
    history = memory.assessments(saver)
    threads = [a.thread_id for a in history]
    decided = items.decided(memory.user_id, memory.profile_id, threads)
    marked = marks.marks(memory.user_id, memory.profile_id, threads)
    return history, decided, marked


def _accepted_earlier(
    memory: Memory,
    saver: BaseCheckpointSaver,
    latest: dict[str, Assessment],
    entries: list[Entry] | None = None,
) -> dict[str, Assessment]:
    """Each control whose newest assessment waits for review: its newest accepted assessment
    before that one, listed since the fresh start (a control with none left out), which the
    comparison counts while the new run waits (#438). Only those controls' runs are loaded."""
    waiting = {i: a.thread_id for i, a in latest.items() if a.status == "waiting-for-review"}
    if not waiting:
        return {}
    load = assessment_loader(saver, scope=memory.scope)
    found: dict[str, Assessment] = {}
    for entry in memory.entries() if entries is None else entries:  # newest first
        control_id = entry.control_id
        if control_id not in waiting or control_id in found:
            continue
        if entry.thread_id == waiting[control_id]:
            continue
        assessment = load(entry.thread_id)
        if assessment is not None and assessment.status == "accepted":
            found[control_id] = assessment
    return found


def _comparison(
    review: rounds.ReviewRounds,
    memory: Memory,
    saver: BaseCheckpointSaver,
    controls: list[IsaControl],
    latest: dict[str, Assessment],
) -> report.ReportComparison | None:
    """Now compared with the last round closed since the fresh start (None when there is none):
    the overview's (#402) and the improvement report's (#420), a control assessed again
    counted under its earlier accepted verdict while the new run waits (#438). `controls`: the
    applicable ones (the standing's, #454)."""
    against = review.last_closed(memory.user_id, memory.profile_id, memory.fresh_start())
    if against is None or against.closed_at is None:
        return None
    earlier = _accepted_earlier(memory, saver, latest)
    return {
        "against": {"name": against.name, "closed_at": against.closed_at.isoformat()},
        **rounds.compare(against.results, controls, latest, earlier),
    }


def _verdict_changes_only(
    comparison: report.ReportComparison | None,
) -> report.ReportComparison | None:
    """The comparison as the improvement report shows it: its changed controls only those whose
    accepted verdict changed, a control re-assessing under the same verdict left out (#438), as
    the report's table has no column for it."""
    if comparison is None:
        return None
    changed = [c for c in comparison["changed"] if c["before"] != c["after"]]
    return {**comparison, "changed": changed}


def _rounds(
    review: rounds.ReviewRounds,
    memory: Memory,
    saver: BaseCheckpointSaver,
    controls: list[IsaControl],
    latest: dict[str, Assessment],
) -> dict[str, Any]:
    """The overview's review rounds (#402): the open one (None when none is), and now compared
    with the last round closed since the fresh start (None when there is none)."""
    current = review.current(memory.user_id, memory.profile_id)
    return {
        "current": None if current is None else _opened(current),
        "comparison": _comparison(review, memory, saver, controls, latest),
    }


def _opened(current: rounds.Round) -> dict[str, Any]:
    opened = current.opened_at
    return {"name": current.name, "opened_at": None if opened is None else opened.isoformat()}


@router.post("/rounds")
def start_round(
    request: Annotated[rounds.RoundRequest, Body()],
    review: ReviewRounds,
    profile: Profile,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead starts the next review round under the profile, by name (#402), with its audit
    entry, together (when the entry can't be written, neither is: a 503). While a round is
    open, the first one too, a 409 with nothing written; a blank or longer name a 422. It
    starts no run, so it works while one goes on."""
    try:
        started = rounds.start(
            review, request.name, reviewer=audit.Reviewer(user.id, name), profile_id=profile.id
        )
    except rounds.RoundOpen as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {"current": _opened(started)}


@router.post("/rounds/close")
def close_round(
    review: ReviewRounds,
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead closes the open review round under the profile (#402): its results frozen (each
    control's newest assessment as it stands now) with its audit entry, together (when the
    entry can't be written, neither is: a 503). With none open, a 409 with nothing written. A
    control not applicable as a whole under the profile isn't frozen (#454)."""
    scope, latest, _ = _standing(profile, memory, saver, OVERVIEW_FAILED)
    try:
        closed = rounds.close(
            review,
            rounds.freeze(scope.applicable, latest),
            reviewer=audit.Reviewer(user.id, name),
            profile_id=profile.id,
        )
    except rounds.NoRoundOpen as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    assert closed.closed_at is not None
    return {"closed": {"name": closed.name, "closed_at": closed.closed_at.isoformat()}}


def _listed(listed: rounds.ListedRound) -> dict[str, Any]:
    opened, closed = listed.opened_at, listed.closed_at
    return {
        "number": listed.number,
        "name": listed.name,
        "opened_at": None if opened is None else opened.isoformat(),
        "closed_at": None if closed is None else closed.isoformat(),
        "before_fresh_start": listed.before_fresh_start,
    }


@router.get("/rounds")
def list_rounds(review: ReviewRounds, memory: UserMemory) -> dict[str, Any]:
    """The user's review rounds under the profile, oldest first (the open one last), each
    numbered from 1 in the order opened, and the default choice to compare with (#571): the
    number of the last round closed since the fresh start, or None."""
    listed = review.listed(memory.user_id, memory.profile_id, memory.fresh_start())
    return {"rounds": [_listed(r) for r in listed], "default": rounds.default_choice(listed)}


@router.get("/rounds/{number}/comparison")
def round_comparison(
    number: int,
    review: ReviewRounds,
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    marks: RemediationPlan,
    asked: OpenQuestions,
) -> dict[str, Any]:
    """The round comparison (#571): now against the closed round numbered `number`, control by
    control, with readiness then and now, and the remediation items done and open questions
    answered since it closed. An unknown number is a 404; the open round, or one closed before
    the fresh start, a 409."""
    fresh_start = memory.fresh_start()
    listed = review.listed(memory.user_id, memory.profile_id, fresh_start)
    if not 1 <= number <= len(listed):
        raise HTTPException(status_code=404, detail="No review round has that number.")
    chosen = listed[number - 1]
    closed_at = chosen.closed_at
    if closed_at is None:
        raise HTTPException(status_code=409, detail=rounds.STILL_OPEN)
    if chosen.before_fresh_start:
        raise HTTPException(status_code=409, detail=rounds.BEFORE_FRESH_START)
    scope, latest, _ = _standing(profile, memory, saver, OVERVIEW_FAILED)
    entries = memory.entries()  # newest first, since the fresh start
    earlier = _accepted_earlier(memory, saver, latest, entries)
    control_of = {e.thread_id: e.control_id for e in entries}
    marked = marks.marks(memory.user_id, memory.profile_id, list(control_of))
    in_scope = {c.id for c in scope.applicable}
    since_fresh = questions.since(asked.listed(memory.user_id, memory.profile_id), fresh_start)
    done = rounds.done_since(control_of, marked, closed_at, in_scope)
    answered = rounds.answered_since(since_fresh, closed_at, in_scope)
    return {
        "round": _listed(chosen),
        **rounds.round_comparison(
            chosen.results, scope.controls, scope.applicable, latest, earlier
        ),
        "remediation_done": [d | {"at": d["at"].isoformat()} for d in done],
        "questions_answered": [a | {"at": a["at"].isoformat()} for a in answered],
    }


def _control_of(profile: ComplianceProfile, control_id: str, failed: str) -> IsaControl:
    """The profile's control, else a 404; an unreadable edition a plain 500 with `failed`."""
    try:
        return profile.isa_control(control_id)
    except UnknownControlError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BaseFrameworkError:
        raise _unreadable_edition(profile, failed) from None


@router.post("/controls/{control_id}/questions")
def log_question(
    control_id: str,
    request: Annotated[questions.QuestionRequest, Body()],
    asked: OpenQuestions,
    profile: Profile,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead logs an open question on one of the profile's controls, assessed or not (#414):
    what, for whom, due when; kept with its audit entry, together (when the entry can't be
    written, neither is: a 503). The question as kept, redacted.

    404 for a control that isn't one of the profile's; 422 for a blank, longer or NUL-holding
    question or `asked_of`, a missing field or no body, or a due date that isn't a date or is
    past everywhere or beyond the horizon (the hint in `detail`)."""
    control = _control_of(profile, control_id, QUESTION_FAILED)
    try:
        kept = questions.log(
            asked,
            control.id,
            request,
            reviewer=audit.Reviewer(user.id, name),
            profile_id=profile.id,
        )
    except remediation.DueDateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    clean, _ = redact_data({"question": questions.shown(kept, audit.now())})
    return clean


@router.post("/controls/{control_id}/questions/{question_id}/answer")
def answer_question(
    control_id: str,
    question_id: Annotated[int, Path(ge=1, le=MAX_QUESTION_ID)],
    request: Annotated[questions.AnswerRequest, Body()],
    asked: OpenQuestions,
    profile: Profile,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead records the answer to their open question on the control (#414), with its audit
    entry, together (when the entry can't be written, neither is: a 503); the question as kept,
    redacted. An answer is final: the same answer again writes nothing.

    404 for an unknown question, another user's or profile's, or one on another control; 409
    for a question answered already with another answer (`detail` names it); 422 for a blank,
    longer or missing answer."""
    try:
        kept = questions.answer(
            asked,
            control_id,
            question_id,
            request.answer,
            reviewer=audit.Reviewer(user.id, name),
            profile_id=profile.id,
        )
    except questions.UnknownQuestionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except questions.AnsweredAlreadyError as error:  # it names the kept answer: redacted
        raise HTTPException(status_code=409, detail=redact(str(error))[0]) from error
    clean, _ = redact_data({"question": questions.shown(kept, audit.now())})
    return clean


@router.put("/goal")
def change_goal(
    request: Annotated[goal.GoalRequest, Body()],
    goals: ProfileGoals,
    profile: Profile,
    user: CurrentUser,
    name: ReviewerName,
) -> dict[str, Any]:
    """The lead changes their Goal under the compliance profile (#369): another goal than the
    one kept is kept with its audit entry, together (when the entry can't be written, neither
    is: a 503); the goal kept already again writes nothing. Any other value is a 422."""
    goal.change(goals, request.goal, reviewer=audit.Reviewer(user.id, name), profile_id=profile.id)
    return {"goal": request.goal}


class ImprovementStores(NamedTuple):
    """What the improvement report (#420) reads beyond the readiness report's stores: the Goal
    that picks the shape, the plan's marks, the review rounds and the open questions."""

    goals: goal.ProfileGoals
    marks: plan.RemediationPlan
    review: rounds.ReviewRounds
    asked: questions.OpenQuestions


def improvement_stores(
    goals: ProfileGoals, marks: RemediationPlan, review: ReviewRounds, asked: OpenQuestions
) -> ImprovementStores:
    return ImprovementStores(goals, marks, review, asked)


Improvement = Annotated[ImprovementStores, Depends(improvement_stores)]


def _report(
    profile: ComplianceProfile,
    memory: Memory,
    saver: BaseCheckpointSaver,
    items: remediation.RemediationItems,
    stores: ImprovementStores,
    read_trail: Callable[[], audit.Page],
    failed: str,
) -> dict[str, Any]:
    """The report for the profile, redacted (vouch.report): its standing first (an unreadable
    ISA edition is the plain 500 with `failed`), then the trail `read_trail` reads. Under the
    user's Goal: the readiness report, or in Internal improvement the improvement report (#420),
    its plan, comparison and questions read as the overview reads them."""
    scope, latest, accepted = _standing(profile, memory, saver, failed)
    current = stores.goals.goal(memory.user_id, memory.profile_id)
    built: Mapping[str, Any]
    if current != plan.IMPROVEMENT:
        decided = items.decided(memory.user_id, memory.profile_id, accepted)
        built = report.readiness_report(
            profile, scope, latest, decided=decided, trail=read_trail(), today=report.today()
        )
    else:
        history, decided, marked = _plan_reads(memory, saver, items, stores.marks)
        listed = questions.since(
            stores.asked.listed(memory.user_id, memory.profile_id), memory.fresh_start()
        )
        built = report.improvement_report(
            profile,
            scope,
            latest,
            history=history,
            decided=decided,
            marks=marked,
            flags=plan.card_flags(history, decided, marked, current),
            comparison=_verdict_changes_only(
                _comparison(stores.review, memory, saver, scope.applicable, latest)
            ),
            questions=listed,
            trail=read_trail(),
            today=report.today(),
        )
    clean, _ = redact_data(dict(built))
    return clean


@router.get("/report")
def readiness_report(
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    items: RemediationItems,
    stores: Improvement,
    trail: AuditTrail,
) -> dict[str, Any]:
    """The report for the user's compliance profile (vouch.report), in its answer language,
    with the report's words; its `goal` names its shape. Under Audit readiness the readiness
    report (#234): readiness with the overview's accepted counts (its remediation count is of
    the approved items the report lists), each control's reviewed verdict, the
    approved remediation items, the not-applicable list, the newest page of the audit trail
    (`trail.next` reads on at GET /api/audit-trail) and the disclaimer. In Internal improvement
    the improvement report (#420): readiness, the comparison with the last round closed since the
    fresh start, the plan by priority, the open questions, the trail's newest page and the
    internal self-assessment disclaimer. No model runs. Dated the server's date in UTC; an
    unreadable ISA edition is a plain 500. The data is redacted as every answer is; the words,
    the same for every report, are not."""

    def newest_page() -> audit.Page:
        return audit.read(trail, memory.user_id, memory.profile_id)

    built = _report(profile, memory, saver, items, stores, newest_page, REPORT_FAILED)
    return built | {"words": report.words_for(profile.answer_language)}


@router.get("/documents")
def profile_documents_tab(
    profile: Profile, memory: UserMemory, saver: Checkpointer
) -> dict[str, Any]:
    """The Documents tab (#424), for every signed-in user: each evidence document the profile
    uses, with its summary, whether it is stale today and the controls whose newest assessment
    cites it (vouch.documents_tab; a control not applicable as a whole under the profile names
    none, #454), and the tab's words in the profile's language. Redacted as the overview is;
    an unreadable ISA edition, folder or document a plain 500 naming no path."""
    scope, latest, _ = _standing(profile, memory, saver, DOCUMENTS_FAILED)
    try:
        listed = documents_tab.profile_documents(profile, scope.applicable, latest, report.today())
    except (OSError, ValueError):  # ValueError: a file not UTF-8, a date YAML refuses
        log.exception("the documents of %s could not be read", profile.id)
        raise HTTPException(status_code=500, detail=DOCUMENTS_FAILED) from None
    clean, _ = redact_data({"documents": [d.as_dict() for d in listed]})
    language, words = documents_tab.document_words(profile.answer_language)
    return clean | {"words": words, "words_language": language}


@router.get("/company")
def company_page(profile: Profile) -> dict[str, Any]:
    """The Company page's facts (#505, vouch.company_page), for every signed-in user, under the
    current company: its profile's facts, overlays, protection level's scope, not-applicable
    entries and where it is defined. Read-only; redacted as the overview is. The base framework
    unreadable, a plain 500 naming no file."""
    try:
        facts = company_facts(profile)
    except BaseFrameworkError:
        raise _unreadable_edition(profile, COMPANY_FAILED) from None
    clean, _ = redact_data(facts)
    return clean


def attachment(name: str) -> str:
    """A Content-Disposition for a download named `name`: `filename` an ASCII slug of it
    (letters, digits, `-` and `.` kept, lower case) for any browser, `filename*` the name as
    it is, in UTF-8 (RFC 6266, RFC 5987), so no character of a profile id breaks the header."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    stem, dot, extension = ascii_name.rpartition(".")
    slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", stem if dot else ascii_name))
    fallback = f"{slug.strip('-') or 'download'}{dot}{re.sub(r'[^a-z0-9]', '', extension)}"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def _export(
    profile: ComplianceProfile,
    memory: Memory,
    saver: BaseCheckpointSaver,
    items: remediation.RemediationItems,
    stores: ImprovementStores,
    trail: audit.AuditTrail,
    write: Callable[[dict[str, Any], dict[str, Any]], str | bytes],
    extension: str,
    media_type: str,
) -> Response:
    """A download of the report: the same redacted report and words as GET /api/report, but
    with the whole audit trail, newest first, not a page, written by `write` (report, words).
    An attachment named `readiness-report-<profile>-<date>.<extension>`, or in Internal
    improvement `improvement-report-…` (#420) (`attachment`); an unreadable ISA edition is a
    plain 500 with EXPORT_FAILED."""

    def whole_trail() -> audit.Page:
        return audit.Page(audit.read_all(trail, memory.user_id, memory.profile_id), None)

    built = _report(profile, memory, saver, items, stores, whole_trail, EXPORT_FAILED)
    shape = "improvement" if built["goal"] == plan.IMPROVEMENT else "readiness"
    name = f"{shape}-report-{profile.id}-{built['head']['date']}.{extension}"
    return Response(
        write(built, report.words_for(profile.answer_language)),
        media_type=media_type,
        headers={"Content-Disposition": attachment(name)},
    )


@router.get("/report/markdown")
def readiness_report_markdown(
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    items: RemediationItems,
    stores: Improvement,
    trail: AuditTrail,
) -> Response:
    """The report as one Markdown file (vouch.report.markdown, #57), as `_export` makes every
    download."""
    return _export(
        profile, memory, saver, items, stores, trail, report.markdown, "md", "text/markdown"
    )


@router.get("/report/pdf")
def readiness_report_pdf(
    profile: Profile,
    memory: UserMemory,
    saver: Checkpointer,
    items: RemediationItems,
    stores: Improvement,
    trail: AuditTrail,
) -> Response:
    """The report as one PDF file built on the server (vouch.report_pdf, #308), as `_export`
    makes every download: the same data and failures as the Markdown file."""
    return _export(
        profile, memory, saver, items, stores, trail, report_pdf.pdf, "pdf", "application/pdf"
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


CHAT_HISTORY_UNAVAILABLE = (
    "Your earlier conversation can't be shown just now. New messages work as usual."
)
"""Said where the kept conversation can't be read (#251): the chat opens empty, never an error. Must
match HISTORY_UNREACHABLE in frontend/src/chat.ts, said where it can't be reached at all."""


class ShownTurn:
    """One chat turn's lines as the chat shows them (#251), built from the events it is sent: the
    lead's message, Vouch's words (with a cross-control answer's working papers, #217), each
    step, and each finished assessment's verdict last, as the chat adds it after the words. Must
    match how the chat builds its lines from the same events (AskVouch.tsx `converse`): a new
    event the chat shows needs a line here too, or a restored conversation reads differently."""

    def __init__(self, message: str | None) -> None:
        self.started = time.time_ns()  # its place in the conversation: when the turn began
        self.lines: list[dict[str, Any]] = []
        self.verdicts: list[dict[str, Any]] = []
        self.papers: list[str] | None = None
        if message is not None:
            self.lines.append({"from": "lead", "text": redact(message)[0]})

    def note(self, event: str, data: dict[str, Any]) -> None:
        if event == "token":
            last = self.lines[-1] if self.lines else None
            if last is not None and last["from"] == "vouch" and not last.get("failed"):
                last["text"] += data["text"]
            else:
                self.lines.append({"from": "vouch", "text": data["text"]})
            if self.papers is not None:
                self.lines[-1]["papers"] = self.papers
        elif event == "step":
            self.lines.append({"from": "step", "step": data})
        elif event == "papers":
            self.papers = data["control_ids"]
        elif event == "assessment":
            self.verdicts.append(
                {
                    "from": "verdict",
                    "control_id": data["control_id"],
                    "verdict": data["verdict"],
                    "status": data["status"],
                    "answer_language": data.get("answer_language"),
                }
            )
        elif event == "error":
            self.lines.append({"from": "vouch", "text": data["message"], "failed": True})

    def shown(self) -> list[dict[str, Any]]:
        return self.lines + self.verdicts


@router.get("/chat/current")
def current_conversation(memory: UserMemory) -> dict[str, Any]:
    """The conversation the chat opens on under this user and profile (#251): its thread and its
    lines as they were shown; none yet, no thread. Kept on the server, so on any device. Only
    the user's own: there is no reading another thread by its id. Unreadable, it opens empty
    with a note, never an error."""
    try:
        thread_id = memory.current_chat()
        return {"thread_id": thread_id, "lines": memory.chat_lines(thread_id) if thread_id else []}
    except Exception:  # the chat must open whatever the store says
        log.exception("the kept conversation could not be read")
        return {"thread_id": None, "lines": [], "note": CHAT_HISTORY_UNAVAILABLE}


@router.delete("/chat/current")
def new_conversation(memory: UserMemory) -> dict[str, Any]:
    """A new conversation (#251): the chat opens on none until the next message starts one. The
    old one is kept on the server."""
    try:
        memory.set_current_chat(None)
    except Exception:  # the chat clears its own view all the same
        log.exception("the new conversation could not be noted")
    return {"thread_id": None}


@router.post("/progress/fresh-start")
def fresh_start(memory: UserMemory, running: Running) -> dict[str, Any]:
    """Start the lead's progress under the profile afresh (#285, vouch.memory's
    `Memory.start_afresh`): only what they start from now on is listed and counted, and the chat
    opens a new conversation. Nothing is deleted, and the audit trail keeps every entry. No model
    runs and nothing counts against the daily limit. While one of the lead's assessments runs
    under the profile, a 409 with nothing written; the lead's scope is held meanwhile, so none
    starts mid-way. A store that can't be written is a 503 (DatabaseUnavailable)."""
    if not running.try_hold(memory.scope):
        raise HTTPException(status_code=409, detail=RUNNING_NO_FRESH_START)
    try:
        at = memory.start_afresh()
    finally:
        running.release(memory.scope)
    return {"fresh_start": at.isoformat()}


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
    metered as one from the working paper is. With the Langfuse keys set, the turn is one trace, and
    an assessment it starts another (vouch.tracing, #460)."""
    meter = UsageMeter(chosen)
    thread_id = request.thread_id or new_thread_id()
    turn = ShownTurn(request.message)
    # The lead's own message makes this the conversation the chat opens on next time (#251); a
    # greeting alone never does, so a stale or failed greeting isn't what comes back.
    if request.message is not None:
        try:
            memory.set_current_chat(thread_id)
        except Exception:  # the chat answers whether or not it can be kept
            log.exception("the conversation could not be noted as current")

    def record(control_id: str, usage: Usage) -> None:
        record_off_the_event_loop(spending, user.id, control_id, usage)

    yield ServerSentEvent(event="thread", data={"thread_id": thread_id})
    try:
        tools = ChatTools(profile, memory, run, spending, running, record)
        chat = chat_context(
            models.for_chat(chosen, callbacks=[meter], answer_language=profile.answer_language),
            tools,
            chat_prompt(profile, context),
        )
        message = request.message or GREETING_REQUEST
        facts = TraceFacts(
            tracing.CHAT_TURN,
            user.id,
            profile.id,
            chosen,
            CHAT_PROMPT_VERSION,
            control_id=request.control_id,
            session_id=thread_id,
        )
        with models.traced(facts) as run_config:
            graph = chat_graph(run.saver)
            for event, data in chat_turn(graph, chat, message, thread_id, memory.scope, run_config):
                turn.note(event, data)
                yield ServerSentEvent(event=event, data=data)
        yield ServerSentEvent(event="done", data={})
    except Exception:  # any failure reaches the browser as one formal sentence
        log.exception("the chat could not answer")
        turn.note("error", {"message": CHAT_FAILED})
        yield ServerSentEvent(event="error", data={"message": CHAT_FAILED})
    finally:
        try:
            # As shown, so it comes back the same; keyed by when the turn began.
            memory.add_chat_turn(thread_id, turn.shown(), turn.started)
        except Exception:  # never at the cost of the answer
            log.exception("the chat turn could not be kept")
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
    models: ModelChoice,
    spending: SpendingGuard,
    settings: Settings,
    companies: CompanyProfiles,
    current: CurrentCompanyStore,
    model: str | None = None,
    *,
    rows: list[company_profiles.StoredProfile] | None = None,
) -> dict[str, Any]:
    """The whole developer page, the same for its read and for each change's answer (#455: the
    changes once left out the broken profiles, and the page rendering their answer failed),
    the companies made in setup (#418) and the current company (#480) included. `rows`: the
    companies made in setup when the caller has just read them, so they aren't read twice."""
    page = models.settings(model) | spending.settings()
    return page | _company_settings(settings, companies, current, rows=rows)


@router.get("/developer/settings", dependencies=DEVELOPERS)
def developer_settings(
    models: AppModels,
    spending: AppSpending,
    settings: AppSettings,
    user: CurrentUser,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
) -> dict[str, Any]:
    """The model, where assessments run (and why on the fake), the prompt version, the daily
    limit, every assessment's usage totalled, the compliance profiles left out of the list
    because they can't be loaded (#47), each with its problem, the companies made in setup
    (#418), and the listed and current companies (#480)."""
    return _developer_page(models, spending, settings, companies, current)


def _company_settings(
    settings: Settings,
    companies: CompanyProfiles,
    current: CurrentCompanyStore,
    *,
    rows: list[company_profiles.StoredProfile] | None = None,
) -> dict[str, Any]:
    """The developer page's companies: `broken_profiles`; `companies`, each live company made in
    setup, oldest first, broken and shadowed ones included, so any can be removed (#418), None
    when they can't be read (logged); `listed_companies`, every company listed for users as
    GET /api/profiles lists them, and `current_company`, the current one's id (#480). `rows`:
    the companies made in setup as just read, else they are read here."""
    if rows is None:
        rows = _stored_companies(companies)
    scan = _profile_scan(settings, rows)
    made = None if rows is None else [_made_company(row) for row in rows]
    return {
        "broken_profiles": [asdict(b) for b in scan.broken],
        "companies": made,
        "listed_companies": [listed_profile(p) for p in scan.loaded],
        "current_company": current_of(scan, stored_current_company(current)),
    }


def _made_company(row: company_profiles.StoredProfile) -> dict[str, str]:
    """A company made in setup as the developer page lists it: its id, its name, when made."""
    name = row.profile.get("company")
    return {
        "id": row.id,
        "company": name if isinstance(name, str) else row.id,
        "created_at": row.created_at.isoformat(),
    }


@router.put("/developer/daily-limit", dependencies=DEVELOPERS)
def change_daily_limit(
    request: DailyLimitRequest,
    models: AppModels,
    spending: AppSpending,
    settings: AppSettings,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
) -> dict[str, Any]:
    """Each user may start `daily_limit` assessments a day, from the next one on. The answer is
    the whole developer page, as `developer_settings` reads it (#455)."""
    spending.set_daily_limit(request.daily_limit)
    return _developer_page(models, spending, settings, companies, current)


@router.put("/developer/model", dependencies=DEVELOPERS)
def change_model(
    request: ModelRequest,
    models: AppModels,
    spending: AppSpending,
    settings: AppSettings,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
) -> dict[str, Any]:
    """The next assessment runs on `model`, once OpenRouter's model list has it. The answer is
    the whole developer page, as `developer_settings` reads it (#455)."""
    try:
        model = models.change(request.model)
    except NoKey:
        raise HTTPException(status_code=409, detail=NO_KEY) from None
    except UnknownModel:
        detail = UNKNOWN_MODEL.format(model=request.model)
        raise HTTPException(status_code=422, detail=detail) from None
    except CatalogueUnavailable:
        raise HTTPException(status_code=503, detail=CATALOGUE_UNAVAILABLE) from None
    return _developer_page(models, spending, settings, companies, current, model)


class CurrentCompanyRequest(BaseModel):
    company: Annotated[str, StringConstraints(max_length=200)]


@router.put("/developer/current-company", dependencies=DEVELOPERS)
def change_current_company(
    request: CurrentCompanyRequest,
    models: AppModels,
    spending: AppSpending,
    settings: AppSettings,
    user: CurrentUser,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
) -> dict[str, Any]:
    """Make `company` the current company (#480), every user's from their next page open. Only a
    listed company: an unknown, removed or broken one's id is a 404, nothing changed. The
    companies made in setup or the setting can't be read or written: a 503, nothing changed.
    The answer is the whole developer page, as `developer_settings` reads it (#455)."""
    rows = companies.all()  # unreadable: a 503, never a wrong 404
    if request.company not in {p.id for p in _profile_scan(settings, rows).loaded}:
        raise HTTPException(status_code=404, detail=UNKNOWN_PROFILE)
    current.set(request.company)
    log.info("the current company is now %s, made so by %s", request.company, user.id)
    return _developer_page(models, spending, settings, companies, current, rows=rows)


@router.get("/developer/protection-levels", dependencies=DEVELOPERS)
def protection_levels() -> dict[str, Any]:
    """What each protection level brings into scope (#403), for company setup's step 3; the
    base framework unreadable, a plain 500 naming no file."""
    try:
        levels = protection_scope()
    except BaseFrameworkError:
        log.exception("the base framework could not be read")
        raise HTTPException(status_code=500, detail=PROTECTION_LEVELS_FAILED) from None
    return {"levels": [asdict(scope) for scope in levels]}


@router.get("/developer/controls", dependencies=DEVELOPERS)
def framework_controls() -> dict[str, Any]:
    """Every control of the base framework's English edition, in its order (#407), for company
    setup's step 5; the base framework unreadable, a plain 500 naming no file."""
    try:
        controls = load_base_framework().controls
    except BaseFrameworkError:
        log.exception("the base framework could not be read")
        raise HTTPException(status_code=500, detail=CONTROLS_FAILED) from None
    return {"controls": [{"id": c.id, "question": c.question} for c in controls]}


@router.get("/developer/prompts", dependencies=DEVELOPERS)
def developer_prompts(
    settings: AppSettings,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
    control: str | None = None,
) -> dict[str, Any]:
    """Every prompt the agent gets (#496), built for the current company, always (the request's
    profile header isn't read), and `control`, one of its applicable controls (default: the
    first): `company`, `controls` (every applicable one, for the picker), `control` and the six
    `prompts` (vouch.prompts). A control not applicable to the company, or unknown, is a 404
    naming it, nothing built; the base framework unreadable, a plain 500 naming no file."""
    control = control or None  # `?control=` asks for none: the first applies
    profile = _current_profile(settings, companies, current)
    try:
        controls = applicable_controls(profile, profile.isa_edition().controls)
    except BaseFrameworkError:
        raise _unreadable_edition(profile, PROMPTS_FAILED) from None
    chosen = next((c for c in controls if c.id == control), None) if control else None
    if control is not None and chosen is None:
        raise HTTPException(status_code=404, detail=NOT_A_PROMPT_CONTROL.format(control=control))
    chosen = chosen or (controls[0] if controls else None)
    if chosen is None:  # every control not applicable: company setup refuses such a company
        raise HTTPException(status_code=404, detail=NO_PROMPT_CONTROL)
    return {
        "company": {"id": profile.id, "name": profile.company},
        "controls": [{"id": c.id, "question": c.question} for c in controls],
        "control": {"id": chosen.id, "question": chosen.question},
        "prompts": [p.to_dict() for p in prompt_previews(profile, chosen)],
    }


@router.get("/developer/sample-library", dependencies=DEVELOPERS)
def sample_library() -> dict[str, Any]:
    """Every document of the sample library (#408), policies then records, each by title, with
    what Vouch reads from it (DocumentSummary) and whether it is stale today, for company
    setup's step 6; the library unreadable, a plain 500 naming no path."""
    try:
        summaries = library_summaries(SAMPLE_LIBRARY_DIR, LIBRARY_SOURCES, report.today())
    except (OSError, ValueError):  # ValueError: a file not UTF-8, a date YAML refuses
        log.exception("the sample library could not be read")
        raise HTTPException(status_code=500, detail=SAMPLE_LIBRARY_FAILED) from None
    return {"documents": [asdict(s) | {"stale": s.stale_because is not None} for s in summaries]}


@router.post("/developer/profiles", dependencies=DEVELOPERS, status_code=201)
def create_company_profile(
    setup: company_profiles.CompanySetup,
    settings: AppSettings,
    user: CurrentUser,
    companies: CompanyProfiles,
) -> dict[str, str]:
    """A company made in the app (company setup, #385), its evidence chosen documents of the
    sample library, kept and listed beside the file profiles; answers it as GET /api/profiles
    lists it. A profile the checks refuse is a 422 naming the field, with nothing kept; the
    database down, a 503."""
    # Checked before it is kept, so a refused one keeps nothing. Its id is known only once
    # kept (the store takes the first free one, racing creates included), and a stored
    # profile's checks don't depend on its id, so it is checked under the id its name gives.
    try:
        profile = company_profiles.compliance_profile_of(
            company_profiles.company_id(setup.company), setup
        )
    except ProfileError as error:
        raise HTTPException(status_code=422, detail=load_problem(error)) from None
    taken = profile_folder_names(settings.profiles_dir)
    fields = setup.model_dump(mode="json")
    profile_id = companies.create(setup.company, fields, user.id, taken)
    log.info("company profile %s created by %s", profile_id, user.id)
    return listed_profile(profile) | {"id": profile_id}


@router.delete("/developer/profiles/{profile_id}", dependencies=DEVELOPERS)
def remove_company_profile(
    profile_id: str,
    models: AppModels,
    spending: AppSpending,
    settings: AppSettings,
    user: CurrentUser,
    companies: CompanyProfiles,
    current: AppCurrentCompany,
) -> dict[str, Any]:
    """Remove a company made in setup (#418): it leaves every list and can't be opened, its row
    kept so its id is never used again, its audit trail, threads, remediation items and Goal
    left as they are. Answers the developer settings after it; when they can't be read then,
    `settings_unavailable` (still a 200: the removal is done). A stored company is removed
    even when a profile folder of its id was added since (the file profile stays). A profile
    folder's name with no such company is a 409; any other id a 404; the database down a 503,
    nothing changed. An assessment already streaming under it runs to its end."""
    if companies.remove(profile_id, user.id):
        log.info("company profile %s removed by %s", profile_id, user.id)
    elif profile_id in profile_folder_names(settings.profiles_dir):
        raise HTTPException(status_code=409, detail=NOT_A_MADE_COMPANY)
    else:
        raise HTTPException(status_code=404, detail=UNKNOWN_PROFILE)
    _forget_profile(profile_id)
    try:
        return _developer_page(models, spending, settings, companies, current)
    except Exception:  # removed already: never answer as if nothing was (#418 review)
        log.exception("the developer settings can't be read after removing %s", profile_id)
        return {"settings_unavailable": True}


def _forget_profile(profile_id: str) -> None:
    """Drop what this process keeps under a removed company's id (#418), so the caches don't grow
    with removals; the id is never used again."""
    _paper_requirements.pop(profile_id, None)
    company_profiles.read_stored_once.forget(profile_id)


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
    app.add_exception_handler(RequestValidationError, request_refused)
    app.add_exception_handler(DailyLimitReached, daily_limit_reached)
    app.add_middleware(MessageLimit)  # security layer 1: no oversized message reaches a route
    remember_secrets(settings)  # security layer 4: its own secret values never leave it
    app.state.settings = settings
    app.state.rate_limit = RateLimit(requests=settings.rate_requests)
    app.state.auth = auth
    app.state.database = Database(settings.database_url) if settings.database_url else None
    app.state.models = ModelChoice(settings, model_store(app.state.database))
    app.state.current_company = current_company_store(app.state.database)
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
# Once: by import time uvicorn's handlers exist. vouch's INFO lines written, every line redacted
# (security layer 4), then the startup line, which needs both (#536).
configure_logging()
announce_tracing(app.state.settings)
