import { useEffect, useRef, useState } from 'react'
import type { Control } from './assessment.ts'
import AskVouch from './AskVouch.tsx'
import { developerSettings, type DeveloperSettings, type UNAVAILABLE } from './developer.ts'
import DeveloperPage from './DeveloperPage.tsx'
import Dossier from './Dossier.tsx'
import { controlPath, useOverview } from './overview.ts'
import TopBar from './TopBar.tsx'
import { useAssessment } from './useAssessment.ts'
import WorkingPaper from './WorkingPaper.tsx'

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

/** The view an address shows; the Dossier for one it does not know. */
function viewAt(path: string): View {
  if (path === DEVELOPER_PATH) return { name: 'developer' }
  const control = CONTROL_PATH.exec(path)
  return control ? { name: 'paper', controlId: decodeURIComponent(control[1]) } : { name: 'dossier' }
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
  const { load } = overview
  const onDossier = view.name === 'dossier'
  // Read on opening, then afresh on each return to the Dossier (not on leaving it: each read counts to the rate limit).
  const read = useRef(false)
  useEffect(() => {
    if (onDossier || !read.current) void load()
    read.current = true
  }, [load, onDossier])
  // A control's working paper needs its question from the overview; a control the overview doesn't list shows the
  // Dossier.
  const control = view.name === 'paper' && overview.state.status === 'ready'
    ? overview.state.overview.controls.find((c) => c.id === view.controlId)
    : undefined
  const shown: View['name'] =
    view.name === 'developer' && developer ? 'developer'
      : view.name === 'paper' && (control || overview.state.status !== 'ready') ? 'paper'
        : 'dossier'
  // Set by the Dossier's "Assess" next step: the working paper starts the assessment as it opens.
  const [assessOnOpen, setAssessOnOpen] = useState(false)
  const open = (controlId: string, assess: boolean) => {
    setAssessOnOpen(assess)
    showView({ name: 'paper', controlId })
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
            <ControlPaper key={control.id} company={overview.state.overview.company}
              control={{ id: control.id, title: control.question }} assessOnOpen={assessOnOpen} />
          </main>
        )
          : <Dossier state={overview.state} onRetry={overview.retry} onOpen={open} />}
      <AskVouch />
    </>
  )
}

/** One control's working paper and its assessment run; `assessOnOpen` starts the run as it opens. */
function ControlPaper({ company, control, assessOnOpen }: { company: string; control: Control; assessOnOpen: boolean }) {
  const { run, start } = useAssessment(control.id)
  const startOnce = useRef(assessOnOpen)
  useEffect(() => {
    if (startOnce.current) void start()
    startOnce.current = false
  }, [start])
  return <WorkingPaper company={company} control={control} run={run} onAssess={() => void start()} />
}
