import { useCallback, useEffect, useRef, useState } from 'react'
import { type Assessment, newestAssessment } from './assessment.ts'
import AskVouch from './AskVouch.tsx'
import { developerSettings, type DeveloperSettings, type UNAVAILABLE } from './developer.ts'
import DeveloperPage from './DeveloperPage.tsx'
import Dossier from './Dossier.tsx'
import { controlPath, useOverview } from './overview.ts'
import TopBar from './TopBar.tsx'
import { type Run, useAssessment } from './useAssessment.ts'
import WorkingPaper, { ControlNotFound } from './WorkingPaper.tsx'

type Theme = 'light' | 'dark'

/** The system setting, or light when there is none. */
function systemTheme(): Theme {
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

/** `onSignOut`: in clerk mode, a Sign out button in the top bar; local mode has nobody to sign out. */
export default function App({ onSignOut }: { onSignOut?: () => void } = {}) {
  const [theme, setTheme] = useState<Theme>(systemTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  const other: Theme = theme === 'light' ? 'dark' : 'light'
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
  useEffect(() => {
    if (paperId === null || !readStored) return
    const answer = { wanted: true } // until the paper changes
    void newestAssessment(paperId).then((assessment) => answer.wanted && setStored({ controlId: paperId, assessment }))
    return () => { answer.wanted = false }
  }, [paperId, readStored])
  const idle: Run = { status: 'idle', steps: [] }
  const paperRun: Run | null = runFor === paperId && run.status !== 'idle' ? run
    : !readStored ? idle
      : stored?.controlId !== paperId ? null
        : stored.assessment ? { status: 'done', steps: [], assessment: stored.assessment } : idle
  const open = (controlId: string, assess: boolean) => {
    showView({ name: 'paper', controlId })
    if (assess) void start(controlId)
  }

  return (
    <>
      <TopBar>
        <nav className="nav" aria-label="Main">
          <a href="/" aria-current={shown === 'dossier' ? 'page' : undefined}
            onClick={(e) => { e.preventDefault(); showView({ name: 'dossier' }) }}>Dossier</a>
          {developer && (
            <a href={DEVELOPER_PATH} aria-current={shown === 'developer' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); showView({ name: 'developer' }) }}>Developer</a>
          )}
        </nav>
        <button className="btn btn-ghost theme" onClick={() => setTheme(other)}>
          {other === 'dark' ? 'Dark theme' : 'Light theme'}
        </button>
        {onSignOut && <button className="btn btn-ghost" onClick={onSignOut}>Sign out</button>}
      </TopBar>
      {shown === 'developer' && developer ? <main><DeveloperPage settings={developer} onChange={setDeveloper} /></main>
        : control && overview.state.status === 'ready' ? (
          <main>
            <WorkingPaper overview={overview.state.overview} card={control} running={running}
              run={paperRun ?? idle} checking={paperRun === null}
              onAssess={() => void start(control.id)} onOpen={(id) => open(id, false)}
              onOverview={() => showView({ name: 'dossier' })} />
          </main>
        )
          : notFound !== null ? <main><ControlNotFound controlId={notFound} onOverview={() => showView({ name: 'dossier' })} /></main>
          : <Dossier state={overview.state} onRetry={overview.retry} onOpen={open} running={running} />}
      <AskVouch />
    </>
  )
}
