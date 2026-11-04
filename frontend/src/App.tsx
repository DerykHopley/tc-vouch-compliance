import { useEffect, useState } from 'react'
import type { Control } from './assessment.ts'
import AskVouch from './AskVouch.tsx'
import { useAssessment } from './useAssessment.ts'
import WorkingPaper, { AssessButton, RunChip } from './WorkingPaper.tsx'

type Theme = 'light' | 'dark'

/** The system setting, or light when there is none. */
function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

// The trial's company and one of its controls, until M1 brings profiles and the ISA catalogue.
const COMPANY = 'Nordlicht Komponenten GmbH'
const CONTROL: Control = { id: 'NK-ENC-01', title: 'Laptops are encrypted', owner: 'Information security officer' }

type View = 'dossier' | 'paper'

/** The view an address shows: the control's working paper at /controls/<id>, else the Dossier. */
const PAPER_PATH = `/controls/${CONTROL.id}`
const viewAt = (path: string): View => (path === PAPER_PATH ? 'paper' : 'dossier')

export default function App() {
  const [theme, setTheme] = useState<Theme>(systemTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  const other: Theme = theme === 'light' ? 'dark' : 'light'
  const [view, setView] = useState<View>(() => viewAt(window.location.pathname))
  useEffect(() => {
    const onBack = () => setView(viewAt(window.location.pathname))
    window.addEventListener('popstate', onBack)
    return () => window.removeEventListener('popstate', onBack)
  }, [])
  /** Shows a view and gives it its address, so a refresh or a shared link opens it again. */
  const go = (next: View) => {
    const path = next === 'paper' ? PAPER_PATH : '/'
    if (window.location.pathname !== path) window.history.pushState(null, '', path)
    setView(next)
  }
  const { run, start } = useAssessment(CONTROL.id)
  const assess = () => {
    go('paper')
    void start()
  }

  return (
    <>
      <header className="top">
        <div className="top-in wrap">
          <div className="brand"><b>Vouch</b><span>Audit preparation</span></div>
          <nav className="nav" aria-label="Main">
            <a href="/" aria-current={view === 'dossier' ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); go('dossier') }}>Dossier</a>
          </nav>
          <button className="btn btn-ghost theme" onClick={() => setTheme(other)}>
            {other === 'dark' ? 'Dark theme' : 'Light theme'}
          </button>
        </div>
      </header>
      {view === 'paper' ? <main><WorkingPaper company={COMPANY} control={CONTROL} run={run} onAssess={() => void start()} /></main> : (
      <main className="dossier wrap">
        <section className="hero">
          <span className="pill">{COMPANY} · Germany</span>
          <h1>Prepared for audit. One piece of evidence at a time.</h1>
          <p><b>0 of 1 controls accepted.</b> Start with {CONTROL.id}: assess it and review what Vouch finds.</p>
        </section>
        <section className="band" aria-labelledby="readiness">
          <div><span className="pill">Where you stand</span><h2 id="readiness">Readiness</h2></div>
          <p>Only verdicts you accepted count; the figures describe evidence coverage, not audit results.</p>
          <div className="tiles">
            <div className="tile"><b>0</b><span>accepted of 1</span></div>
            <div className="tile"><b>0</b><span>evidenced</span></div>
            <div className="tile"><b>0</b><span>partly evidenced</span></div>
            <div className="tile"><b>0</b><span>not evidenced</span></div>
            <div className="tile"><b>1</b><span>not assessed</span></div>
          </div>
        </section>
        <section className="controls">
          <div className="head"><span className="pill">The controls</span><h2>One control to begin</h2></div>
          <div className="cards">
            <article className="card" aria-labelledby="control-title">
              <div className="card-head"><b>{CONTROL.id}</b></div>
              <div id="control-title" className="control-title">{CONTROL.title}</div>
              <div className="card-meta"><span>{CONTROL.owner}</span><RunChip run={run} /></div>
              <div><AssessButton control={CONTROL} run={run} onAssess={assess} /></div>
            </article>
          </div>
        </section>
      </main>
      )}
      <AskVouch />
    </>
  )
}
