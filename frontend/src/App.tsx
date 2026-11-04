import { useEffect, useState } from 'react'
import AskVouch from './AskVouch.tsx'

type Theme = 'light' | 'dark'

/** The system setting, or light when there is none. */
function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

// One control from the trial's fixtures until M1 brings the ISA catalogue.
const CONTROL = { id: 'NK-ENC-01', title: 'Laptops are encrypted', owner: 'Information security officer' }

export default function App() {
  const [theme, setTheme] = useState<Theme>(systemTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  const other: Theme = theme === 'light' ? 'dark' : 'light'

  return (
    <>
      <header className="top">
        <div className="top-in wrap">
          <div className="brand"><b>Vouch</b><span>Audit preparation</span></div>
          <nav className="nav" aria-label="Main">
            <a href="/" aria-current="page">Dossier</a>
          </nav>
          <button className="btn btn-ghost theme" onClick={() => setTheme(other)}>
            {other === 'dark' ? 'Dark theme' : 'Light theme'}
          </button>
        </div>
      </header>
      <main className="dossier wrap">
        <section className="hero">
          <span className="pill">Nordlicht Komponenten GmbH · Germany</span>
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
              <div className="card-meta"><span>{CONTROL.owner}</span><span className="chip v-none">Not assessed</span></div>
            </article>
          </div>
        </section>
      </main>
      <AskVouch />
    </>
  )
}
