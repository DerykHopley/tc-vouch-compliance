import { useCallback, useEffect, useRef, useState } from 'react'
import { useHeightVar } from './useHeightVar.ts'
import { type Assessment, newestAssessment, storedAssessment } from './assessment.ts'
import { type Arrival, useFirstPass } from './arrival.ts'
import AskVouch from './AskVouch.tsx'
import type { PageContext } from './chat.ts'
import { developerSettings, type DeveloperSettings, UNAVAILABLE } from './developer.ts'
import CompanyPage from './CompanyPage.tsx'
import CompanySetupWizard from './CompanySetupWizard.tsx'
import DeveloperPage from './DeveloperPage.tsx'
import Dossier from './Dossier.tsx'
import { mayLeave, unsavedCount } from './leaving.ts'
import { controlPath, isReviewState, useOverview } from './overview.ts'
import { type PassAssessment, useProfilePass } from './profilePass.ts'
import type { ProfileSummary } from './profiles.ts'
import { type RemediationItem, withItem } from './remediation.ts'
import type { Goal } from './report.ts'
import Report from './Report.tsx'
import TopBar from './TopBar.tsx'
import UserMenu from './UserMenu.tsx'
import type { Theme } from './user.ts'
import { type Run, useAssessment } from './useAssessment.ts'
import RoundComparisonView from './RoundComparison.tsx'
import { forgetScrolls } from './roundComparison.ts'
import { COMPANY_PATH, roundPath } from './rounds.ts'
import { settleSetupEntry } from './setupHistory.ts'
import WorkingPaper, { ControlNotFound } from './WorkingPaper.tsx'


/** The theme the page already shows (the app opens afresh on a profile switch), else the system setting, else light. */
function startingTheme(): Theme {
  const shown = document.documentElement.dataset.theme
  if (shown === 'light' || shown === 'dark') return shown
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

type View = { name: 'dossier' } | { name: 'report' } | { name: 'company' } | { name: 'developer' } | { name: 'setup' }
  | { name: 'paper'; controlId: string } | { name: 'round'; number: number }

export const DEVELOPER_PATH = '/developer'
export const SETUP_PATH = '/developer/setup'
const REPORT_PATH = '/report'
const DOCUMENTS_PATH = '/documents' // the documents' own page before #495: it leads to the Company tab
const CONTROL_PATH = /^\/controls\/([^/]+)$/
const ROUND_PATH = /^\/company\/rounds\/([1-9]\d{0,8})$/

/** Each view's address: a control's working paper at /controls/<id>, the readiness report at /report, the Company page
 * at /company (#495, #505), the developer page at /developer and its company setup at /developer/setup (#403), the
 * Dossier at /. */
const pathOf = (view: View) =>
  view.name === 'paper' ? controlPath(view.controlId) : view.name === 'report' ? REPORT_PATH
    : view.name === 'company' ? COMPANY_PATH : view.name === 'round' ? roundPath(view.number) : view.name === 'developer' ? DEVELOPER_PATH
      : view.name === 'setup' ? SETUP_PATH : '/'

/** The page the chat says the lead is on for the view shown: company setup is the developer page's, and the Company
 * page the documents' (#495), so the chat knows only its Documents panel, not the facts, scope or not-applicable
 * panels (#505 left the chat's page out of scope; a 'company' page would change the chat's prompt and its version).
 * The not-found page (no view shown) speaks as the Dossier, where its way back leads. */
const chatPage = (shown: View['name'] | null): PageContext['page'] =>
  shown === 'setup' ? 'developer' : shown === 'company' || shown === 'round' ? 'documents' : shown ?? 'dossier'

/** The view an address shows; the Dossier for one it does not know, and the Company tab for /documents, the documents'
 * old address (#495). An id with a broken escape stays as written, so its paper says not found. */
function viewAt(path: string): View {
  if (path === DEVELOPER_PATH) return { name: 'developer' }
  if (path === SETUP_PATH) return { name: 'setup' }
  if (path === REPORT_PATH) return { name: 'report' }
  if (path === COMPANY_PATH || path === DOCUMENTS_PATH) return { name: 'company' }
  const round = ROUND_PATH.exec(path)
  if (round) return { name: 'round', number: Number(round[1]) }
  const control = CONTROL_PATH.exec(path)
  if (!control) return { name: 'dossier' }
  try {
    return { name: 'paper', controlId: decodeURIComponent(control[1]) }
  } catch {
    return { name: 'paper', controlId: control[1] }
  }
}

/** What a company setup's create did while the developer was on another page (#569), said under the top bar: `open`,
 * when it made `company` current, opens it. */
export type CreatedNotice = { text: string; open?: { company: string; onOpen: () => void } }

/** After Dismiss on the created notice (#569), whose button is gone: focus goes to the page's h1, or else its main,
 * where the page is scrolled to now. */
function focusPage() {
  const main = document.querySelector<HTMLElement>('main')
  const target = main?.querySelector<HTMLElement>('h1') ?? main
  if (!target) return
  if (!target.hasAttribute('tabindex')) target.tabIndex = -1
  target.focus({ preventScroll: true })
}

type AppProps = {
  /** Clerk mode's sign out, in the user menu; local mode has nobody to sign out. */
  onSignOut?: () => void
  /** The signed-in user's name, for the user menu (#222); none in local mode. */
  userName?: string
  /** The current company's summary (#495): the top bar and the user menu's fresh start (#285) name it until the
   * overview is read, then the overview's company. None without a profile list. */
  profile?: ProfileSummary
  /** The fresh start went through (#285): ProfiledApp opens the app afresh. */
  onStartedAfresh?: () => void
  /** Company setup made a company (#409), with the Goal chosen in it (#447): ProfiledApp makes it current (#480) and
   * opens the app afresh under it; `entry` is the wizard's history entry, which gives way (#476, #568). */
  onCompanyCreated?: (profile: ProfileSummary, goal: Goal, entry: string) => void
  /** A company made in setup was removed on the developer page (#418), with the page's note: ProfiledApp reads the
   * profile list again, and opens the app afresh under the current company when it changed (#480). */
  onCompanyRemoved?: (id: string, note: string) => void
  /** A company was made current on the developer page (#480), with the page's note: ProfiledApp opens the app afresh
   * under it. */
  onMadeCurrent?: (id: string, note: string) => void
  /** The app opened afresh on the developer page with a note for it (a removal, a Make current, a company made but not
   * made current): the developer page says it, once. */
  developerNote?: string
  /** The developer page said developerNote. */
  onNoteShown?: () => void
  /** The app opens on a company just made: the Dossier's heading takes focus, until the first move elsewhere (#409),
   * and the arrival sequence sets the Goal chosen in setup and runs a profile pass (#447). Only a create sets it; it is
   * taken once, as the app mounts, and `onArrivalUsed` then lets ProfiledApp clear it, so no later mount reruns it. */
  arrival?: Arrival | null
  onArrivalUsed?: () => void
  /** A create landed while the developer was elsewhere (#569): said under the top bar, the page left as it is, until
   * Dismiss (`onNoticeDismissed`) or the app opens afresh. */
  createdNotice?: CreatedNotice
  onNoticeDismissed?: () => void
  /** Back or Forward reached the setup entry of a create that landed while the developer was elsewhere: ProfiledApp
   * opens the company it made, `id` (#568, #569). */
  onSetupReached?: (id: string) => void
}

/** The app: the top bar (#222, the owner's option D: page tabs and the user menu, behind one Menu button on a phone;
 * no profile switcher since #480), then the page shown, and the chat. */
export default function App({ onSignOut, userName, profile, onStartedAfresh, onCompanyCreated, onCompanyRemoved,
  onMadeCurrent, developerNote, onNoteShown, arrival = null, onArrivalUsed, createdNotice, onNoticeDismissed,
  onSetupReached }: AppProps = {}) {
  const [theme, setTheme] = useState<Theme>(startingTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  const other: Theme = theme === 'light' ? 'dark' : 'light'
  const [barOpen, setBarOpen] = useState(false) // the phone's top bar menu
  // Each count moves focus to the Menu button: a page opened from the phone's panel hides the tab or item it came from.
  const [toToggle, setToToggle] = useState(0)
  useEffect(() => {
    if (toToggle > 0) document.querySelector<HTMLButtonElement>('.bar-toggle')?.focus()
  }, [toToggle])
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
  const [view, setView] = useState<View>(() => {
    const { pathname, search, hash } = window.location
    // An old link to the documents (#424) opens the Company tab, at its own address, with no new history entry (#495).
    if (pathname === DOCUMENTS_PATH) window.history.replaceState(null, '', `${COMPANY_PATH}${search}${hash}`)
    return viewAt(pathname)
  })
  const [focusHeading, setFocusHeading] = useState(arrival !== null) // ends with the first move to another view
  // The round comparison a working paper was opened from (#573): that control's breadcrumb leads back there, until
  // another view; another control's paper, reached by a link from it, has the Overview's.
  const [paperFrom, setPaperFrom] = useState<{ number: number; name: string; controlId: string } | null>(null)
  /** Off to a view other than a paper or a comparison: no breadcrumb back, and no comparison's old scroll (#573). */
  const leaveRounds = () => {
    setPaperFrom(null)
    forgetScrolls()
  }
  const onSetupReachedRef = useRef(onSetupReached) // read by the popstate listener, set up once
  useEffect(() => {
    onSetupReachedRef.current = onSetupReached
  }, [onSetupReached])
  // The notice band's height, for the scroll padding and the sticky parts below it (app.css).
  const noticeBand = useRef<HTMLDivElement>(null)
  useHeightVar(noticeBand, '--created-notice', createdNotice !== undefined)
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
      const settled = settleSetupEntry() // a finished setup's entry gives way (#568)
      const next = viewAt(settled.path)
      if (next.name !== 'paper' && next.name !== 'round') leaveRounds()
      setView(next)
      if (settled.opens !== null) onSetupReachedRef.current?.(settled.opens) // the company it made opens (#569)
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
    if (next.name !== 'paper') setPaperFrom(null)
    if (next.name !== 'paper' && next.name !== 'round') forgetScrolls()
    if (window.location.pathname !== path) window.history.pushState(null, '', path)
    setFocusHeading(false)
    setView(next)
    return true
  }
  // A developer's settings (or that they're unavailable), or null: then the user menu has no Developer page or Set up a
  // company, and /developer and /developer/setup show the Dossier.
  const [developer, setDeveloper] = useState<DeveloperSettings | typeof UNAVAILABLE | null>(null)
  useEffect(() => {
    let current = true
    void developerSettings().then((settings) => current && setDeveloper(settings))
    return () => { current = false }
  }, [])
  // A create's notice (#569): the developer settings are read again, so the developer page lists the company made
  // (and offers Make current, when it could not be made current); a read that fails keeps the page as it is.
  useEffect(() => {
    if (!createdNotice) return
    let current = true
    void developerSettings().then((settings) => {
      if (current && settings && settings !== UNAVAILABLE) setDeveloper(settings)
    })
    return () => { current = false }
  }, [createdNotice])
  const overview = useOverview()
  const { load, noteAssessed, refresh: refreshOverview } = overview
  const onDossier = view.name === 'dossier'
  // Read on opening, then afresh on each return to the Dossier (not on leaving it: each read counts to the rate limit).
  // The report shows none of it (#56): it is read on leaving it for a view that does. The Company page's Goal and
  // review round panels act on it (#505), so it is read afresh on each arrival there too, as on the Dossier: another
  // tab may have changed them. A read that fails there keeps the page (CompanyPage keeps the overview last read).
  const needsOverview = view.name !== 'report'
  const onCompany = view.name === 'company'
  const read = useRef(false)
  useEffect(() => {
    if (!needsOverview) return
    if (onDossier || onCompany || !read.current) void load()
    read.current = true
  }, [load, onDossier, onCompany, needsOverview])
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
  const shown: View['name'] | null = view.name === 'report' || view.name === 'company' || view.name === 'round'
    ? view.name
    : (view.name === 'developer' || view.name === 'setup') && developer ? view.name : control ? 'paper'
    : notFound !== null ? null : 'dossier'
  // The paper's control's newest stored assessment (#38), read as its paper opens; null while that read is out. None
  // is read for a control its card says is not assessed (a run in this page puts its result on the card), nor for the
  // one this page's run is of, which holds its result: each read counts to the rate limit.
  const paperId = control ? control.id : null
  const ownRun = runFor === control?.id && run !== overtaken // the paper's control's run in this page, still its newest
  // A control not assessed, or not applicable as a whole (#454), has nothing stored to read.
  const readStored = control !== undefined && isReviewState(control.status) && !ownRun
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
  // A company just made (#447): its Goal is set, then its first pass runs, once (useFirstPass). The arrival is used
  // up as the app opens.
  const { arriving, goalNotSet, goalSet } = useFirstPass(arrival, { overview: overview.state,
    goalChanged: overview.goalChanged, pass, startPass })
  useEffect(() => {
    if (arrival !== null) onArrivalUsed?.()
  }, [arrival, onArrivalUsed])
  const { goalChanged } = overview
  const changedGoal = useCallback((goal: Goal) => {
    goalSet()
    return goalChanged(goal)
  }, [goalChanged, goalSet])
  useEffect(() => {
    if (paperId === null || !readStored) return
    const answer = { wanted: true } // until the paper changes
    void newestAssessment(paperId).then((assessment) => answer.wanted && setStored({ controlId: paperId, assessment }))
    return () => { answer.wanted = false }
  }, [paperId, readStored, paperReloads])
  // The lead's decisions made in this page (#42), by thread: newer than the run's result or the stored read.
  const [reviewed, setReviewed] = useState<Record<string, Assessment>>({})
  // The overview once read: its goal and language name the top bar's label (#440), and its company the bar's company
  // (#495), so the bar and the page never disagree; until it is read, the listed company, none without a list.
  const readOverview = overview.state.status === 'ready' ? overview.state.overview : undefined
  const barCompany = readOverview ?? profile
  // In the Internal improvement goal a review or a plan mark can change a card's flags (#410: re-assessment
  // suggested, Closed), which the server works out: the overview is read again, kept as it is when that read fails.
  const improving = readOverview?.goal === 'internal-improvement'
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
  /** A tab or a menu item shows `next`; on a phone, the bar's panel then closes, focus on its Menu button. When the lead
   * stays (#182), the panel stays open as it was. */
  const go = (next: View) => {
    if (!showView(next)) return
    if (barOpen) setToToggle((n) => n + 1)
    setBarOpen(false)
  }
  const open = (controlId: string, assess: boolean) => {
    if (showView({ name: 'paper', controlId }) && assess && !passing) void start(controlId)
  }

  return (
    <>
      <TopBar goal={readOverview?.goal} language={readOverview?.answer_language} company={barCompany}>
        <button className="btn btn-ghost bar-toggle" aria-expanded={barOpen} aria-controls="top-bar-menu"
          onClick={() => setBarOpen((now) => !now)}>Menu</button>
        <div id="top-bar-menu" className={barOpen ? 'bar-menu open' : 'bar-menu'}>
          <nav className="tabs" aria-label="Main">
            <a href="/" aria-current={shown === 'dossier' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); go({ name: 'dossier' }) }}>Overview</a>
            <a href={REPORT_PATH} aria-current={shown === 'report' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); go({ name: 'report' }) }}>Report</a>
            <a href={COMPANY_PATH} aria-current={shown === 'company' || shown === 'round' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); go({ name: 'company' }) }}>Company</a>
          </nav>
          <UserMenu name={userName} theme={theme} onTheme={() => setTheme(other)} onSignOut={onSignOut}
            company={barCompany?.company} onStartedAfresh={onStartedAfresh}
            developerTools={developer
              ? { onPage: () => go({ name: 'developer' }), onSetup: () => go({ name: 'setup' }) } : undefined} />
        </div>
      </TopBar>
      {/* Mounted empty before any notice, so a sentence put in its status region is announced (#569). */}
      <div ref={noticeBand} className={createdNotice ? 'wrap created-notice shown' : 'created-notice'}>
        <div role="status">{createdNotice && <p className="field-note notice">{createdNotice.text}</p>}</div>
        {createdNotice?.open && (
          <button type="button" className="btn btn-primary" onClick={createdNotice.open.onOpen}>
            Open {createdNotice.open.company}</button>
        )}
        {createdNotice && (
          <button type="button" className="btn btn-ghost" onClick={() => {
            onNoticeDismissed?.()
            focusPage()
          }}>Dismiss</button>
        )}
      </div>
      {shown === 'report' ? <Report />
        : shown === 'company' ? (
          <CompanyPage overview={overview.state} onRetryOverview={overview.retry} onGoalChanged={changedGoal}
            onRoundChanged={overview.roundChanged} arriving={arriving} goalNotSet={goalNotSet}
            documentsReloads={documentsReloads} />
        )
        : view.name === 'round' ? (
          <RoundComparisonView key={view.number} number={view.number}
            names={overview.state.status === 'ready' ? overview.state.overview : undefined}
            onRounds={() => showView({ name: 'company' })} onOpen={(controlId, name) => {
              if (showView({ name: 'paper', controlId })) setPaperFrom({ number: view.number, name, controlId })
            }} />
        )
        : shown === 'developer' && developer ? (
          <main>
            <DeveloperPage settings={developer} onChange={setDeveloper} onSetup={() => showView({ name: 'setup' })}
              onRemoved={onCompanyRemoved} onMadeCurrent={onMadeCurrent} developerNote={developerNote}
              onNoteShown={onNoteShown} />
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
              back={paperFrom?.controlId === control.id ? { label: `Compared with ${paperFrom.name}`, href: roundPath(paperFrom.number),
                onBack: () => showView({ name: 'round', number: paperFrom.number }) } : undefined}
              onItemDecided={noteItem} onQuestionsChanged={() => void refreshOverview()} />
          </main>
        )
          : notFound !== null ? <main><ControlNotFound controlId={notFound} onOverview={() => showView({ name: 'dossier' })} /></main>
          : <Dossier state={overview.state} onRetry={overview.retry} onOpen={open} running={running}
            focusHeading={focusHeading} pass={pass} onPass={() => void startPass()}
            onGoalChanged={changedGoal} onRoundChanged={overview.roundChanged}
            arriving={arriving} goalNotSet={goalNotSet} />}
      {/* An assessment from the chat reads the overview afresh (its card, readiness), its paper's newest if open, and
          the documents if the Company tab shows them (their Cited by, #424). */}
      <AskVouch context={{ page: chatPage(shown),
        controlId: shown === 'paper' && control ? control.id : null }}
        onOpen={(controlId) => open(controlId, false)}
        language={overview.state.status === 'ready' ? overview.state.overview.answer_language : undefined}
        onAssessed={(controlId) => {
          void load()
          if (controlId === paperId) setPaperReloads((n) => n + 1)
          if (shown === 'company') setDocumentsReloads((n) => n + 1)
        }} />
    </>
  )
}
