import { useEffect, useState } from 'react'
import type { Verdict } from './assessment.ts'
import CitedQuote from './CitedQuote.tsx'
import Prose from './Prose.tsx'
import { fetchReport, olderEntries, type Report as Served, type ReportControl, type ReportWords, type TrailEntry }
  from './report.ts'
import VerdictChip from './VerdictChip.tsx'

/** The readiness report (#56): the served report (`GET /api/report`) drawn as a formal document, after the
 * prototype's report page. Every heading, column name, status, action and sentence of the report is from the served
 * `words`, in the profile's language, and the document carries that `lang`; the page around it stays English.
 * Read each time the page opens. */

type State = { status: 'loading' } | { status: 'ready'; report: Served } | { status: 'failed'; message: string }

export default function Report() {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [reads, setReads] = useState(0) // bumped by Try again to read the report again
  useEffect(() => {
    const answer = { wanted: true } // until the page closes or reads again
    void fetchReport().then((read) => answer.wanted && setState(read.ok ? { status: 'ready', report: read.report }
      : { status: 'failed', message: read.message }))
    return () => { answer.wanted = false }
  }, [reads])
  const load = () => {
    setState({ status: 'loading' })
    setReads((n) => n + 1)
  }

  if (state.status === 'loading') {
    return <main className="report wrap"><p className="lbl" role="status">Opening the readiness report…</p></main>
  }
  if (state.status === 'failed') {
    return (
      <main className="report wrap">
        <section className="sheet notice">
          <p role="alert" className="answer">{state.message}</p>
          <button className="btn" onClick={load}>Try again</button>
        </section>
      </main>
    )
  }
  return <main className="report wrap"><Document report={state.report} /></main>
}

/** The verdicts in the readiness bar's order, as the Dossier's band. */
const SEGMENTS: Verdict[] = ['evidenced', 'partly-evidenced', 'not-evidenced', 'not-applicable']

/** An ISO time's day, in UTC: `2026-11-06`. */
const dayOf = (iso: string) => new Date(iso).toISOString().slice(0, 10)

/** An ISO time to the minute, in UTC: `2026-11-06 14:05 UTC`. */
const timeOf = (iso: string) => {
  const at = new Date(iso).toISOString()
  return `${at.slice(0, 10)} ${at.slice(11, 16)} UTC`
}

/** A served sentence with its `{name}` places filled in. */
const filled = (sentence: string, values: Record<string, number | string>) =>
  sentence.replace(/\{(\w+)\}/g, (all, name: string) => name in values ? String(values[name]) : all)

function Document({ report }: { report: Served }) {
  const { head, words: w } = report
  const { labels, sentences } = w
  const language = head.answer_language
  return (
    <article className="sheet report-doc" lang={language} aria-labelledby="report-title">
      <header className="report-top">
        <h1 id="report-title">{w.title}</h1>
        <dl className="report-head">
          <dt>{labels.company}</dt><dd>{head.company}</dd>
          <dt>{labels.country}</dt><dd>{head.country}</dd>
          <dt>{labels.base_framework}</dt><dd>{head.base_framework}</dd>
          <dt>{labels.protection_level}</dt><dd>{w.protection_levels[head.protection_level] ?? head.protection_level}</dd>
          <dt>{labels.overlays}</dt>
          <dd>{head.overlays.length ? head.overlays.join(', ') : <span data-word>{sentences.no_overlays}</span>}</dd>
          <dt>{labels.answer_language}</dt><dd>{w.languages[language] ?? language}</dd>
          <dt>{labels.date}</dt><dd className="mono">{head.date}</dd>
        </dl>
      </header>
      <Readiness report={report} />
      <Controls report={report} />
      <Remediation report={report} />
      <NotApplicableList report={report} />
      <Trail report={report} />
      <p className="report-disclaimer">{report.disclaimer}</p>
    </article>
  )
}

function Readiness({ report: { readiness: r, words: w } }: { report: Served }) {
  const acceptedOf = filled(w.sentences.accepted_of, { accepted: r.accepted, controls: r.controls })
  const counts = [
    ...SEGMENTS.map((v) => `${r.by_verdict[v]} ${w.verdicts[v]}`),
    `${r.open} ${w.statuses.open}`,
    `${r.remediation_items} ${w.labels.remediation}`,
  ]
  return (
    <section className="report-part" aria-labelledby="report-readiness">
      <h2 id="report-readiness">{w.labels.readiness}</h2>
      <p className="report-lead" data-word>{acceptedOf}</p>
      <div className="readiness report-bar" role="img" aria-label={acceptedOf}>
        {SEGMENTS.map((v) => r.by_verdict[v] > 0 && (
          <span key={v} className={`readiness-${v}`} style={{ width: `${(r.by_verdict[v] / r.controls) * 100}%` }} />
        ))}
      </div>
      <ul className="report-counts">{counts.map((count) => <li key={count}>{count}</li>)}</ul>
    </section>
  )
}

function Controls({ report: { controls, head, words: w } }: { report: Served }) {
  const { labels } = w
  return (
    <section className="report-part" aria-labelledby="report-controls">
      <h2 id="report-controls">{labels.controls}</h2>
      <div className="table-wrap">
        <table className="report-table report-controls" aria-labelledby="report-controls">
          <thead>
            <tr>
              <th scope="col">{labels.control}</th>
              <th scope="col">{labels.question}</th>
              <th scope="col">{labels.status}</th>
              <th scope="col">{labels.verdict}</th>
              <th scope="col">{labels.reviewer}</th>
              <th scope="col">{labels.reviewed_at}</th>
            </tr>
          </thead>
          <tbody>
            {controls.map((c) => <ControlRows key={c.id} control={c} words={w} language={head.answer_language} />)}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ControlRows({ control: c, words: w, language }: { control: ReportControl; words: ReportWords; language: string }) {
  const accepted = c.status === 'accepted'
  return (
    <>
      <tr>
        <th scope="row" className="mono">{c.id}</th>
        <td className="report-q">{c.question}</td>
        <td><span data-word>{w.statuses[c.status]}</span></td>
        {accepted ? (
          <>
            <td><VerdictChip verdict={c.verdict} language={language} /></td>
            <td>{c.reviewer}</td>
            <td className="mono">{c.reviewed_at ? dayOf(c.reviewed_at) : ''}</td>
          </>
        ) : <td colSpan={3} className="report-muted"><span data-word>{w.sentences.open_control}</span></td>}
      </tr>
      {accepted && c.quotes.length > 0 && (
        <tr className="report-quote-row">
          <td colSpan={6}>
            <section className="report-quotes" aria-label={`${w.labels.quotes} ${c.id}`}>
              <h3 className="lbl">{w.labels.quotes}</h3>
              {c.quotes.map((q, i) => (
                // By position: two quotes may read alike once redacted.
                <CitedQuote key={i} quote={q.quote}>
                  <span className="mono">{q.doc_id} v{q.version} · {q.section}</span>
                  {q.stale && <span className="flag" data-word>{w.labels.stale}</span>}
                </CitedQuote>
              ))}
            </section>
          </td>
        </tr>
      )}
    </>
  )
}

function Remediation({ report: { remediation, words: w } }: { report: Served }) {
  const { labels } = w
  return (
    <section className="report-part" aria-labelledby="report-remediation">
      <h2 id="report-remediation">{labels.remediation}</h2>
      {remediation.length === 0 ? <p className="report-muted" data-word>{w.sentences.no_remediation}</p>
        : remediation.map((group) => (
          <div key={group.control_id} className="report-group">
            <p className="report-group-head"><span className="mono">{group.control_id}</span> {group.question}</p>
            <div className="table-wrap">
              <table className="report-table">
                <thead>
                  <tr>
                    <th scope="col">{labels.requirement}</th>
                    <th scope="col">{labels.missing}</th>
                    <th scope="col">{labels.owner_role}</th>
                    <th scope="col">{labels.due_date}</th>
                  </tr>
                </thead>
                <tbody>
                  {group.items.map((item) => (
                    <tr key={item.requirement_id}>
                      <th scope="row" className="mono">{item.requirement_id}</th>
                      <td className="report-q"><Prose text={item.missing} /></td>
                      <td>{item.owner_role}</td>
                      <td className="mono">{item.due_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
    </section>
  )
}

function NotApplicableList({ report: { not_applicable: listed, words: w } }: { report: Served }) {
  return (
    <section className="report-part" aria-labelledby="report-not-applicable">
      <h2 id="report-not-applicable">{w.labels.not_applicable}</h2>
      {listed.length === 0 ? <p className="report-muted" data-word>{w.sentences.no_not_applicable}</p> : (
        <ul className="report-list">
          {listed.map((r) => (
            <li key={`${r.control_id} ${r.requirement_id}`}>
              <span className="mono">{r.control_id} {r.requirement_id}</span> {r.text}
              <dl className="report-reason"><dt>{w.labels.reason}</dt><dd>{r.reason}</dd></dl>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

type Older = { status: 'idle' } | { status: 'reading' } | { status: 'failed'; message: string }

function Trail({ report: { trail, words: w } }: { report: Served }) {
  const { labels } = w
  const [entries, setEntries] = useState<TrailEntry[]>(trail.entries)
  const [next, setNext] = useState<number | null>(trail.next)
  const [older, setOlder] = useState<Older>({ status: 'idle' })
  const readOlder = async () => {
    if (next === null || older.status === 'reading') return
    setOlder({ status: 'reading' })
    const page = await olderEntries(next)
    if (!page.ok) return setOlder({ status: 'failed', message: page.message })
    setEntries((now) => [...now, ...page.entries])
    setNext(page.next)
    setOlder({ status: 'idle' })
  }
  const verdict = (v: Verdict | null) => v ? <span data-word>{w.verdicts[v]}</span> : null
  return (
    <section className="report-part" aria-labelledby="report-trail">
      <h2 id="report-trail">{labels.trail}</h2>
      {entries.length === 0 ? <p className="report-muted" data-word>{w.sentences.no_trail}</p> : (
        <div className="table-wrap">
          <table className="report-table report-trail" aria-labelledby="report-trail">
            <thead>
              <tr>
                <th scope="col">{labels.at}</th>
                <th scope="col">{labels.who}</th>
                <th scope="col">{labels.control}</th>
                <th scope="col">{labels.requirement}</th>
                <th scope="col">{labels.action}</th>
                <th scope="col">{labels.verdict_before}</th>
                <th scope="col">{labels.verdict_after}</th>
                <th scope="col">{labels.note}</th>
                <th scope="col">{labels.evidence}</th>
                <th scope="col">{labels.model}</th>
                <th scope="col">{labels.prompt_version}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                // By position: an entry's id may be missing, and the pages are appended in order.
                <tr key={i}>
                  <td className="mono report-nowrap">{timeOf(e.at)}</td>
                  <td>{e.reviewer_name}</td>
                  <td className="mono">{e.control_id}</td>
                  <td className="mono">{e.requirement_id ?? ''}</td>
                  <td><span data-word>{w.actions[e.action] ?? e.action}</span></td>
                  <td>{verdict(e.verdict_before)}</td>
                  <td>{verdict(e.verdict_after)}</td>
                  <td className="report-q">{e.note}</td>
                  <td className="mono">
                    {e.evidence.map((d) => <span key={`${d.doc_id} ${d.version}`} className="report-nowrap">{d.doc_id} v{d.version}</span>)}
                  </td>
                  <td className="mono">{e.model}</td>
                  <td className="mono">{e.prompt_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next !== null && (
        <div className="report-older" lang="en">
          <button className="btn" onClick={() => void readOlder()} disabled={older.status === 'reading'}
            aria-busy={older.status === 'reading' ? true : undefined}>Show older entries</button>
          {older.status === 'failed' && <p role="alert" className="field-note notice">{older.message}</p>}
        </div>
      )}
    </section>
  )
}
