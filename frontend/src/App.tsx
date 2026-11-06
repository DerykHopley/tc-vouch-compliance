import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { type Assessment, newestAssessment, storedAssessment } from './assessment.ts'
import AskVouch from './AskVouch.tsx'
import { developerSettings, type DeveloperSettings, type UNAVAILABLE } from './developer.ts'
import DeveloperPage from './DeveloperPage.tsx'
import Dossier from './Dossier.tsx'
import { controlPath, useOverview } from './overview.ts'
import { type RemediationItem, withItem } from './remediation.ts'
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

type View = { name: 'dossier' } | { name: 'developer' } | { name: 'paper'; controlId: string }

const DEVELOPER_PATH = '/developer'
const CONTROL_PATH = /^\/controls\/([^/]+)$/

/** Each view's address: a control's working paper at /controls/<id>, the developer page at /developer, the Dossier at /. */
const pathOf = (view: View) =>
  view.name === 'paper' ? controlPath(view.controlId) : view.name === 'developer' ? DEVELOPER_PATH : '/'

/** The view an address shows; the Dossier for one it does not know. An id with a broken escape stays as written,
 * so its paper says not found. */
function viewAt(path: string): View {
  if (path === DEVELOPER_PATH) return { name: 'developer' }
  const control = CONTROL_PATH.exec(path)
  if (!control) return { name: 'dossier' }
  try {
    return { name: 'paper', controlId: decodeURIComponent(control[1]) }
  } catch {
    return { name: 'paper', controlId: control[1] }
  }
}

type AppProps = {
  /** Clerk mode's sign out, in the user menu; local mode has nobody to sign out. */
  onSignOut?: () => void
  /** The top bar's profile switcher (ProfiledApp.tsx, #47). */
  profileSwitcher?: ReactNode
  /** The signed-in user's name, for the user menu (#222); none in local mode. */
  userName?: string
}

/** The app: the top bar (#222, the owner's option D: page tabs, the profile dropdown and the user menu, behind one
 * Menu button on a phone), then the page shown, and the chat. */
export default function App({ onSignOut, profileSwitcher, userName }: AppProps = {}) {
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
  useEffect(() => {
    const onPopState = () => setView(viewAt(window.location.pathname)) // Back or Forward
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])
  /** Shows a view and gives it its address, so a refresh or a shared link opens it again. */
  const showView = (next: View) => {
    if (window.location.pathname !== pathOf(next)) window.history.pushState(null, '', pathOf(next))
    setView(next)
  }
  // A developer's settings (or that they're unavailable), or null: then there's no Developer link, and /developer
  // shows the Dossier.
  const [developer, setDeveloper] = useState<DeveloperSettings | typeof UNAVAILABLE | null>(null)
  useEffect(() => {
    let current = true
    void developerSettings().then((settings) => current && setDeveloper(settings))
    return () => { current = false }
  }, [])
  const overview = useOverview()
  const { load, noteAssessed } = overview
  const onDossier = view.name === 'dossier'
  // Read on opening, then afresh on each return to the Dossier (not on leaving it: each read counts to the rate limit).
  const read = useRef(false)
  useEffect(() => {
    if (onDossier || !read.current) void load()
    read.current = true
  }, [load, onDossier])
  // The run sits above the views, so it carries on when the lead leaves its paper. A finished run puts its verdict on
  // its card (the working paper's index), and reads the overview afresh when the Dossier is showing (readiness too).
  const finished = useCallback((assessment: Assessment) => {
    noteAssessed(assessment)
    if (viewAt(window.location.pathname).name === 'dossier') void load()
  }, [load, noteAssessed])
  const { run, controlId: runFor, start } = useAssessment(finished)
  const running = run.status === 'running' ? runFor : null
  // A control's working paper needs its question from the overview; an overview not read shows the Dossier, and a
  // control the overview doesn't list says it isn't found.
  const control = view.name === 'paper' && overview.state.status === 'ready'
    ? overview.state.overview.controls.find((c) => c.id === view.controlId)
    : undefined
  const notFound = view.name === 'paper' && overview.state.status === 'ready' && !control ? view.controlId : null
  const shown: View['name'] | null = view.name === 'developer' && developer ? 'developer' : control ? 'paper'
    : notFound !== null ? null : 'dossier'
  // The paper's control's newest stored assessment (#38), read as its paper opens; null while that read is out. None
  // is read for a control its card says is not assessed (a run in this page puts its result on the card), nor for the
  // one this page's run is of, which holds its result: each read counts to the rate limit.
  const paperId = control ? control.id : null
  const readStored = control !== undefined && control.status !== 'not-assessed' && runFor !== control.id
  const [stored, setStored] = useState<{ controlId: string; assessment: Assessment | null } | null>(null)
  const [paperReloads, setPaperReloads] = useState(0) // bumped to read the paper's newest again (the chat assessed it)
  useEffect(() => {
    if (paperId === null || !readStored) return
    const answer = { wanted: true } // until the paper changes
    void newestAssessment(paperId).then((assessment) => answer.wanted && setStored({ controlId: paperId, assessment }))
    return () => { answer.wanted = false }
  }, [paperId, readStored, paperReloads])
  // The lead's decisions made in this page (#42), by thread: newer than the run's result or the stored read.
  const [reviewed, setReviewed] = useState<Record<string, Assessment>>({})
  const noteReviewed = (assessment: Assessment) => {
    if (assessment.thread_id) setReviewed((now) => ({ ...now, [assessment.thread_id!]: assessment }))
    noteAssessed(assessment)
  }
  const idle: Run = { status: 'idle', steps: [] }
  const shownRun: Run | null = runFor === paperId && run.status !== 'idle' ? run
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
    showView({ name: 'paper', controlId })
    if (assess) void start(controlId)
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
            {developer && (
              <a href={DEVELOPER_PATH} aria-current={shown === 'developer' ? 'page' : undefined}
                onClick={(e) => { e.preventDefault(); setBarOpen(false); showView({ name: 'developer' }) }}>Developer</a>
            )}
          </nav>
          {profileSwitcher}
          <UserMenu name={userName} theme={theme} onTheme={() => setTheme(other)} onSignOut={onSignOut} />
        </div>
      </TopBar>
      {shown === 'developer' && developer ? <main><DeveloperPage settings={developer} onChange={setDeveloper} /></main>
        : control && overview.state.status === 'ready' ? (
          <main>
            <WorkingPaper overview={overview.state.overview} card={control} running={running}
              run={paperRun ?? idle} checking={paperRun === null}
              onAssess={() => void start(control.id)} onOpen={(id) => open(id, false)}
              onOverview={() => showView({ name: 'dossier' })} onReviewed={noteReviewed} onRefresh={refresh}
              onItemDecided={noteItem} />
          </main>
        )
          : notFound !== null ? <main><ControlNotFound controlId={notFound} onOverview={() => showView({ name: 'dossier' })} /></main>
          : <Dossier state={overview.state} onRetry={overview.retry} onOpen={open} running={running} />}
      {/* On the not-found page (no view current) the chat speaks as on the Dossier, where its way back leads. An
          assessment from the chat reads the overview afresh (its card, readiness), and its paper's newest if open. */}
      <AskVouch context={{ page: shown ?? 'dossier', controlId: shown === 'paper' && control ? control.id : null }}
        language={overview.state.status === 'ready' ? overview.state.overview.answer_language : undefined}
        onOpen={(controlId) => open(controlId, false)}
        onAssessed={(controlId) => {
          void load()
          if (controlId === paperId) setPaperReloads((n) => n + 1)
        }} />
    </>
  )
}
