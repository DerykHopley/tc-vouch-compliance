import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { type Assessment, newestAssessment, storedAssessment } from './assessment.ts'
import AskVouch from './AskVouch.tsx'
import type { PageContext } from './chat.ts'
import { developerSettings, type DeveloperSettings, UNAVAILABLE } from './developer.ts'
import CompanySetupWizard from './CompanySetupWizard.tsx'
import DeveloperPage from './DeveloperPage.tsx'
import Documents from './Documents.tsx'
import Dossier from './Dossier.tsx'
import { mayLeave, unsavedCount } from './leaving.ts'
import { changeGoal, controlPath, useOverview } from './overview.ts'
import { type PassAssessment, useProfilePass } from './profilePass.ts'
import type { ProfileSummary } from './profiles.ts'
import { type RemediationItem, withItem } from './remediation.ts'
import type { Goal } from './report.ts'
import Report from './Report.tsx'
import TopBar from './TopBar.tsx'
import UserMenu from './UserMenu.tsx'
import type { Theme } from './user.ts'
import { type Run, useAssessment } from './useAssessment.ts'
import WorkingPaper, { ControlNotFound } from './WorkingPaper.tsx'


/** The theme the page already shows (the app opens afresh on a profile switch), else the system setting, else light. */
function startingTheme(): Theme {
  const shown = document.documentElement.dataset.theme
  if (shown === 'light' || shown === 'dark') return shown
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

type View = { name: 'dossier' } | { name: 'report' } | { name: 'documents' } | { name: 'developer' } | { name: 'setup' }
  | { name: 'paper'; controlId: string }

const DEVELOPER_PATH = '/developer'
const SETUP_PATH = '/developer/setup'
const REPORT_PATH = '/report'
const DOCUMENTS_PATH = '/documents'
const CONTROL_PATH = /^\/controls\/([^/]+)$/

/** Each view's address: a control's working paper at /controls/<id>, the readiness report at /report, the profile's
 * documents at /documents (#424), the developer page at /developer and its company setup at /developer/setup (#403),
 * the Dossier at /. */
const pathOf = (view: View) =>
  view.name === 'paper' ? controlPath(view.controlId) : view.name === 'report' ? REPORT_PATH
    : view.name === 'documents' ? DOCUMENTS_PATH : view.name === 'developer' ? DEVELOPER_PATH
      : view.name === 'setup' ? SETUP_PATH : '/'

/** The page the chat says the lead is on for the view shown: company setup is the developer page's, and the
 * not-found page (no view shown) speaks as the Dossier, where its way back leads. */
const chatPage = (shown: View['name'] | null): PageContext['page'] =>
  shown === 'setup' ? 'developer' : shown ?? 'dossier'

/** The view an address shows; the Dossier for one it does not know. An id with a broken escape stays as written,
 * so its paper says not found. */
function viewAt(path: string): View {
  if (path === DEVELOPER_PATH) return { name: 'developer' }
  if (path === SETUP_PATH) return { name: 'setup' }
  if (path === REPORT_PATH) return { name: 'report' }
  if (path === DOCUMENTS_PATH) return { name: 'documents' }
  const control = CONTROL_PATH.exec(path)
  if (!control) return { name: 'dossier' }
  try {
    return { name: 'paper', controlId: decodeURIComponent(control[1]) }
  } catch {
    return { name: 'paper', controlId: control[1] }
  }
}

/** What the app does on opening under a company just made in setup (#447): sets its Goal, then runs a profile pass. */
export type FirstPass = { goal: Goal }

type AppProps = {
  /** Clerk mode's sign out, in the user menu; local mode has nobody to sign out. */
  onSignOut?: () => void
  /** The top bar's profile switcher (ProfiledApp.tsx, #47). */
  profileSwitcher?: ReactNode
  /** The signed-in user's name, for the user menu (#222); none in local mode. */
  userName?: string
  /** The company of the profile shown, for the user menu's fresh start (#285); none without a profile list. */
  company?: string
  /** The fresh start went through (#285): ProfiledApp opens the app afresh. */
  onStartedAfresh?: () => void
  /** Company setup made a company (#409), with the Goal chosen in it (#447): ProfiledApp opens the app afresh under it. */
  onCompanyCreated?: (profile: ProfileSummary, goal: Goal) => void
  /** A company made in setup was removed on the developer page (#418): ProfiledApp reads the profile list again. */
  onCompanyRemoved?: (id: string) => void
  /** The company shown was removed, so the app opened afresh (#418): the developer page says so, once. */
  removedCompany?: string
  /** The developer page said removedCompany. */
  onRemovedShown?: () => void
  /** The app opens on a company just made (#409): the Dossier's heading takes focus, until the first move elsewhere. */
  focusHeading?: boolean
  /** The app opens on a company just made (#447): once, it sets this Goal (unless it is Audit readiness, every
   * profile's default) and then runs a profile pass. Only a create sets it. */
  firstPass?: FirstPass
}

/** The app: the top bar (#222, the owner's option D: page tabs, the profile dropdown and the user menu, behind one
 * Menu button on a phone), then the page shown, and the chat. */
export default function App({ onSignOut, profileSwitcher, userName, company, onStartedAfresh, onCompanyCreated,
  onCompanyRemoved, removedCompany, onRemovedShown, focusHeading: arriving = false, firstPass }: AppProps = {}) {
  const [theme, setTheme] = useState<Theme>(startingTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  const other: Theme = theme === 'light' ? 'dark' : 'light'
  const [barOpen, setBarOpen] = useState(false) // the phone's top bar menu
  useEffect(() => {
    if (!barOpen) return
    // Like the bar's own menus: Esc (unless an inner menu took it) or a tap outside the bar closes the panel.
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setBarOpen(false)
      document.querySelector<HTMLButtonElement>('.bar-toggle')?.focus()
    }
    const outside = (e: PointerEvent) => {
      if (!(e.target as Element).closest('header.top')) setBarOpen(false)
    }
    document.addEventListener('keydown', key)
    document.addEventListener('pointerdown', outside)
    return () => {
      document.removeEventListener('keydown', key)
      document.removeEventListener('pointerdown', outside)
    }
  }, [barOpen])
  const [view, setView] = useState<View>(() => viewAt(window.location.pathname))
  const [focusHeading, setFocusHeading] = useState(arriving) // ends with the first move to another view
  const shownPath = useRef(pathOf(view)) // the address of the view shown, put back when the lead stays (#182)
  useEffect(() => {
    shownPath.current = pathOf(view)
  }, [view])
  useEffect(() => {
    // Back or Forward: the address has already changed, so staying on a paper with unsaved overrides puts it back.
    const onPopState = () => {
      if (window.location.pathname !== shownPath.current && !mayLeave()) {
        window.history.pushState(null, '', shownPath.current)
        return
      }
      setFocusHeading(false)
      setView(viewAt(window.location.pathname))
    }
    // A reload or closing the tab: the browser asks in its own words while overrides are unsaved.
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (unsavedCount() > 0) e.preventDefault()
    }
    window.addEventListener('popstate', onPopState)
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      window.removeEventListener('popstate', onPopState)
      window.removeEventListener('beforeunload', onBeforeUnload)
    }
  }, [])
  /** Shows a view and gives it its address, so a refresh or a shared link opens it again. Leaving a paper with
   * overrides not yet recorded asks first (#182); false when the lead stays. */
  const showView = (next: View) => {
    const path = pathOf(next)
    if (path !== shownPath.current && !mayLeave()) return false // the view shown again asks nothing
    if (window.location.pathname !== path) window.history.pushState(null, '', path)
    setFocusHeading(false)
    setView(next)
    return true
  }
  // A developer's settings (or that they're unavailable), or null: then there's no Developer link, and /developer and
  // /developer/setup show the Dossier.
  const [developer, setDeveloper] = useState<DeveloperSettings | typeof UNAVAILABLE | null>(null)
  useEffect(() => {
    let current = true
    void developerSettings().then((settings) => current && setDeveloper(settings))
    return () => { current = false }
  }, [])
  const overview = useOverview()
  const { load, noteAssessed, refresh: refreshOverview } = overview
  const onDossier = view.name === 'dossier'
  // Read on opening, then afresh on each return to the Dossier (not on leaving it: each read counts to the rate limit).
  // The report and the documents show none of it (#56, #424): it is read on leaving them for a view that does.
  const needsOverview = view.name !== 'report' && view.name !== 'documents'
  const read = useRef(false)
  useEffect(() => {
    if (!needsOverview) return
    if (onDossier || !read.current) void load()
    read.current = true
  }, [load, onDossier, needsOverview])
  // The run sits above the views, so it carries on when the lead leaves its paper. A finished run puts its verdict on
  // its card (the working paper's index), and reads the overview afresh when the Dossier is showing (readiness too).
  const finished = useCallback((assessment: Assessment) => {
    noteAssessed(assessment)
    if (viewAt(window.location.pathname).name === 'dossier') void load()
  }, [load, noteAssessed])
  const { run, controlId: runFor, start } = useAssessment(finished)
  const running = run.status === 'running' ? runFor : null
  // The per-control run a finished profile pass assessed its control again in, if any: it is no longer that control's
  // newest, so its paper reads the stored newest instead (#390). A new run is shown again.
  const [overtaken, setOvertaken] = useState<Run | null>(null)
  const latestRun = useRef({ run, runFor }) // read when a pass ends, which may be renders after it started
  useEffect(() => {
    latestRun.current = { run, runFor }
  }, [run, runFor])
  // A control's working paper needs its question from the overview; an overview not read shows the Dossier, and a
  // control the overview doesn't list says it isn't found.
  const control = view.name === 'paper' && overview.state.status === 'ready'
    ? overview.state.overview.controls.find((c) => c.id === view.controlId)
    : undefined
  const notFound = view.name === 'paper' && overview.state.status === 'ready' && !control ? view.controlId : null
  const shown: View['name'] | null = view.name === 'report' || view.name === 'documents' ? view.name
    : (view.name === 'developer' || view.name === 'setup') && developer ? view.name : control ? 'paper'
    : notFound !== null ? null : 'dossier'
  // The paper's control's newest stored assessment (#38), read as its paper opens; null while that read is out. None
  // is read for a control its card says is not assessed (a run in this page puts its result on the card), nor for the
  // one this page's run is of, which holds its result: each read counts to the rate limit.
  const paperId = control ? control.id : null
  const ownRun = runFor === control?.id && run !== overtaken // the paper's control's run in this page, still its newest
  const readStored = control !== undefined && control.status !== 'not-assessed' && !ownRun
  const [stored, setStored] = useState<{ controlId: string; assessment: Assessment | null } | null>(null)
  const [paperReloads, setPaperReloads] = useState(0) // bumped to read the paper's newest again (the chat assessed it)
  const [documentsReloads, setDocumentsReloads] = useState(0) // bumped to read the documents again (Cited by changed)
  // The profile pass sits above the views too (#390): its result waits on the Dossier. A finished pass reads the
  // overview afresh, so the cards (the working paper's index too) show what it proposed, the open paper's newest, and
  // the documents if shown (their Cited by, #424).
  const passed = useCallback((made: PassAssessment[]) => {
    void load()
    setPaperReloads((n) => n + 1)
    setDocumentsReloads((n) => n + 1)
    const { run: last, runFor: lastFor } = latestRun.current
    if (made.some((a) => a.control_id === lastFor)) setOvertaken(last)
  }, [load])
  const { pass, start: startPass } = useProfilePass(passed)
  const passing = pass.kind === 'running' // one run at a time: a working paper's Assess waits for the pass
  // A company just made (#447): its Goal is set, then its first pass runs, once. The ref outlasts StrictMode's second
  // mount, so it sends one of each; `mounted` keeps a pass from starting after the app has closed (a switch landing
  // while the Goal change is out would name the other profile). A failed change keeps the company and still runs the
  // pass, the Dossier saying the Goal wasn't set; a 401 hands over to the sign-in, with no pass.
  const [goalNotSet, setGoalNotSet] = useState<Goal | null>(null)
  const firstPassStarted = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const { goalChanged } = overview
  useEffect(() => {
    if (!firstPass || firstPassStarted.current) return
    firstPassStarted.current = true
    const { goal } = firstPass
    void (async () => {
      if (goal !== 'audit-readiness') {
        const result = await changeGoal(goal)
        if (!mounted.current || result === 'signed-out') return
        if (result === 'changed') void goalChanged(goal)
        else setGoalNotSet(goal)
      }
      void startPass()
    })()
  }, [firstPass, goalChanged, startPass])
  useEffect(() => {
    if (paperId === null || !readStored) return
    const answer = { wanted: true } // until the paper changes
    void newestAssessment(paperId).then((assessment) => answer.wanted && setStored({ controlId: paperId, assessment }))
    return () => { answer.wanted = false }
  }, [paperId, readStored, paperReloads])
  // The lead's decisions made in this page (#42), by thread: newer than the run's result or the stored read.
  const [reviewed, setReviewed] = useState<Record<string, Assessment>>({})
  // In the Internal improvement goal a review or a plan mark can change a card's flags (#410: re-assessment
  // suggested, Closed), which the server works out: the overview is read again, kept as it is when that read fails.
  const improving = overview.state.status === 'ready' && overview.state.overview.goal === 'internal-improvement'
  const noteReviewed = (assessment: Assessment) => {
    if (assessment.thread_id) setReviewed((now) => ({ ...now, [assessment.thread_id!]: assessment }))
    noteAssessed(assessment)
    if (improving && assessment.status !== 'waiting-for-review') void refreshOverview()
  }
  const idle: Run = { status: 'idle', steps: [] }
  const shownRun: Run | null = ownRun && run.status !== 'idle' ? run
    : !readStored ? idle
      : stored?.controlId !== paperId ? null
        : stored.assessment ? { status: 'done', steps: [], assessment: stored.assessment } : idle
  const decided = shownRun?.status === 'done' && shownRun.assessment.thread_id
    ? reviewed[shownRun.assessment.thread_id] : undefined
  const paperRun: Run | null = shownRun?.status === 'done' && decided ? { ...shownRun, assessment: decided } : shownRun
  /** A remediation item's decision (#43), put in the newest copy of its thread's assessment when the update runs, so
   * two decisions landing one after the other both stay; the paper's shown copy when there is none yet. */
  const noteItem = (threadId: string, item: RemediationItem) => {
    const shown = paperRun?.status === 'done' ? paperRun.assessment : undefined
    setReviewed((now) => {
      const base = now[threadId] ?? (shown?.thread_id === threadId ? shown : undefined)
      return base ? { ...now, [threadId]: withItem(base, item) } : now
    })
    if (improving && item.done) void refreshOverview()
  }
  /** Reads the paper's assessment again (another tab reviewed it first) and shows it as it now stands; false when it
   * couldn't be read. */
  const refresh = async () => {
    const threadId = paperRun?.status === 'done' ? paperRun.assessment.thread_id : undefined
    const again = threadId ? await storedAssessment(threadId) : null
    if (again) noteReviewed(again)
    return again !== null
  }
  const open = (controlId: string, assess: boolean) => {
    if (showView({ name: 'paper', controlId }) && assess && !passing) void start(controlId)
  }

  return (
    <>
      <TopBar>
        <button className="btn btn-ghost bar-toggle" aria-expanded={barOpen} aria-controls="top-bar-menu"
          onClick={() => setBarOpen((now) => !now)}>Menu</button>
        <div id="top-bar-menu" className={barOpen ? 'bar-menu open' : 'bar-menu'}>
          <nav className="tabs" aria-label="Main">
            <a href="/" aria-current={shown === 'dossier' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); setBarOpen(false); showView({ name: 'dossier' }) }}>Overview</a>
            <a href={REPORT_PATH} aria-current={shown === 'report' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); setBarOpen(false); showView({ name: 'report' }) }}>Report</a>
            <a href={DOCUMENTS_PATH} aria-current={shown === 'documents' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); setBarOpen(false); showView({ name: 'documents' }) }}>Documents</a>
            {developer && (
              <a href={DEVELOPER_PATH} aria-current={shown === 'developer' || shown === 'setup' ? 'page' : undefined}
                onClick={(e) => { e.preventDefault(); setBarOpen(false); showView({ name: 'developer' }) }}>Developer</a>
            )}
          </nav>
          {profileSwitcher}
          <UserMenu name={userName} theme={theme} onTheme={() => setTheme(other)} onSignOut={onSignOut}
            company={company} onStartedAfresh={onStartedAfresh} />
        </div>
      </TopBar>
      {shown === 'report' ? <Report />
        : shown === 'documents' ? <Documents reloads={documentsReloads} />
        : shown === 'developer' && developer ? (
          <main>
            <DeveloperPage settings={developer} onChange={setDeveloper} onSetup={() => showView({ name: 'setup' })}
              onRemoved={onCompanyRemoved} removedCompany={removedCompany} onRemovedShown={onRemovedShown} />
          </main>
        )
        : shown === 'setup' && developer ? (
          <main>
            {/* settings unreadable just now: the developer page's notice, not the wizard */}
            {developer === UNAVAILABLE
              ? <DeveloperPage settings={developer} onChange={setDeveloper} onSetup={() => showView({ name: 'setup' })} />
              : <CompanySetupWizard developerPath={DEVELOPER_PATH} onDeveloper={() => showView({ name: 'developer' })}
                onCreated={onCompanyCreated} />}
          </main>
        )
        : control && overview.state.status === 'ready' ? (
          <main>
            <WorkingPaper overview={overview.state.overview} card={control} running={running}
              run={paperRun ?? idle} checking={paperRun === null}
              passing={passing} onAssess={() => { if (!passing) void start(control.id) }} onOpen={(id) => open(id, false)}
              onOverview={() => showView({ name: 'dossier' })} onReviewed={noteReviewed} onRefresh={refresh}
              onItemDecided={noteItem} onQuestionsChanged={() => void refreshOverview()} />
          </main>
        )
          : notFound !== null ? <main><ControlNotFound controlId={notFound} onOverview={() => showView({ name: 'dossier' })} /></main>
          : <Dossier state={overview.state} onRetry={overview.retry} onOpen={open} running={running}
            focusHeading={focusHeading} pass={pass} onPass={() => void startPass()}
            onGoalChanged={overview.goalChanged} onRoundChanged={overview.roundChanged}
            goalNotSet={goalNotSet ?? undefined} />}
      {/* An assessment from the chat reads the overview afresh (its card, readiness), its paper's newest if open, and
          the documents if shown (their Cited by, #424). */}
      <AskVouch context={{ page: chatPage(shown),
        controlId: shown === 'paper' && control ? control.id : null }}
        onOpen={(controlId) => open(controlId, false)}
        language={overview.state.status === 'ready' ? overview.state.overview.answer_language : undefined}
        onAssessed={(controlId) => {
          void load()
          if (controlId === paperId) setPaperReloads((n) => n + 1)
          if (shown === 'documents') setDocumentsReloads((n) => n + 1)
        }} />
    </>
  )
}
