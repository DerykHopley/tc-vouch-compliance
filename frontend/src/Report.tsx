import { useEffect, useState } from 'react'
import type { Verdict } from './assessment.ts'
import CitedQuote from './CitedQuote.tsx'
import { SEGMENTS } from './overview.ts'
import Prose from './Prose.tsx'
import { fetchReport, olderEntries, type ReadinessReport, type ReportControl, type ReportWords, type TrailEntry }
  from './report.ts'
import VerdictChip from './VerdictChip.tsx'

/** The readiness report (#56): the served report (`GET /api/report`) drawn as a formal document, after the
 * prototype's report page. Every heading, column name, status, action, verdict and sentence of the report is from
 * the served `words`, and the document carries their language (`head.words_language`); the page around it stays
 * English. Read each time the page opens. */

type State = { status: 'loading' } | { status: 'ready'; report: ReadinessReport } | { status: 'failed'; message: string }

export default function Report() {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0) // bumped by Try again to read the report again
  useEffect(() => {
    const answer = { wanted: true } // until the page closes or reads again
    void fetchReport().then((read) => answer.wanted && setState(read.ok ? { status: 'ready', report: read.report }
      : { status: 'failed', message: read.message }))
    return () => { answer.wanted = false }
  }, [attempt])
  const tryAgain = () => {
    setState({ status: 'loading' })
    setAttempt((n) => n + 1)
  }

  if (state.status === 'loading') {
    return <main className="report wrap"><p className="lbl" role="status">Opening the readiness report…</p></main>
  }
  if (state.status === 'failed') {
    return (
      <main className="report wrap">
        <section className="sheet notice">
          <p role="alert" className="answer">{state.message}</p>
          <button className="btn" onClick={tryAgain}>Try again</button>
        </section>
      </main>
    )
  }
  return <main className="report wrap"><Document report={state.report} /></main>
}

/** An ISO time's instant, in UTC, as an ISO string; null for one that doesn't parse. */
const utc = (iso: string) => {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? null : at.toISOString()
}

/** An ISO time's day, in UTC: `2026-11-06`; a time that doesn't parse as written. */
const dayOf = (iso: string) => utc(iso)?.slice(0, 10) ?? iso

/** An ISO time to the minute, in UTC: `2026-11-06 14:05 UTC`; a time that doesn't parse as written. */
const timeOf = (iso: string) => {
  const at = utc(iso)
  return at ? `${at.slice(0, 10)} ${at.slice(11, 16)} UTC` : iso
}

/** A served sentence with its `{name}` places filled in. */
const filled = (sentence: string, values: Record<string, number | string>) =>
  sentence.replace(/\{(\w+)\}/g, (all, name: string) => Object.hasOwn(values, name) ? String(values[name]) : all)

function Document({ report }: { report: ReadinessReport }) {
  const { head, words } = report
  const { labels, sentences } = words
  return (
    <article className="sheet report-doc" lang={head.words_language} aria-labelledby="report-title">
      <header className="report-top">
        <h1 id="report-title">{words.title}</h1>
        <dl className="report-head">
          <dt>{labels.company}</dt><dd>{head.company}</dd>
          <dt>{labels.country}</dt><dd>{head.country}</dd>
          <dt>{labels.base_framework}</dt><dd>{head.base_framework}</dd>
          <dt>{labels.protection_level}</dt><dd>{words.protection_levels[head.protection_level]}</dd>
          <dt>{labels.overlays}</dt>
          <dd>{head.overlays.length ? head.overlays.join(', ') : <span data-word>{sentences.no_overlays}</span>}</dd>
          {/* The words name English and German only: another answer language shows as its code. */}
          <dt>{labels.answer_language}</dt><dd>{words.languages[head.answer_language] ?? head.answer_language}</dd>
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

function Readiness({ report: { readiness: r, words } }: { report: ReadinessReport }) {
  const acceptedOf = filled(words.sentences.accepted_of, { accepted: r.accepted, controls: r.controls })
  const counts: [string, string][] = [
    ...SEGMENTS.map((v): [string, string] => [v, `${r.by_verdict[v]} ${words.verdicts[v]}`]),
    ['open', `${r.open} ${words.statuses.open}`],
    ['remediation', `${r.remediation_items} ${words.labels.remediation}`],
  ]
  return (
    <section className="report-part" aria-labelledby="report-readiness">
      <h2 id="report-readiness">{words.labels.readiness}</h2>
      <p className="report-lead" data-word>{acceptedOf}</p>
      <div className="readiness report-bar" role="img" aria-label={acceptedOf}>
        {SEGMENTS.map((v) => r.by_verdict[v] > 0 && (
          <span key={v} className={`readiness-${v}`} style={{ width: `${(r.by_verdict[v] / r.controls) * 100}%` }} />
        ))}
      </div>
      <ul className="report-counts">{counts.map(([key, count]) => <li key={key}>{count}</li>)}</ul>
    </section>
  )
}

function Controls({ report: { controls, words } }: { report: ReadinessReport }) {
  const { labels } = words
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
            {controls.map((c) => <ControlRows key={c.id} control={c} words={words} />)}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ControlRows({ control: c, words }: { control: ReportControl; words: ReportWords }) {
  return (
    <>
      <tr>
        <th scope="row" className="mono">{c.id}</th>
        <td className="report-q">{c.question}</td>
        <td><span data-word>{words.statuses[c.status]}</span></td>
        {c.status === 'accepted' && c.verdict ? (
          <>
            <td><VerdictChip verdict={c.verdict} label={words.verdicts[c.verdict]} /></td>
            <td>{c.reviewer}</td>
            <td className="mono">{c.reviewed_at ? dayOf(c.reviewed_at) : ''}</td>
          </>
        ) : <td colSpan={3} className="report-muted"><span data-word>{words.sentences.open_control}</span></td>}
      </tr>
      {c.status === 'accepted' && c.quotes.length > 0 && (
        <tr className="report-quote-row">
          <td colSpan={6}>
            <section className="report-quotes" aria-label={`${words.labels.quotes} ${c.id}`}>
              <h3 className="lbl">{words.labels.quotes}</h3>
              {c.quotes.map((q, i) => (
                // By position: two quotes may read alike once redacted.
                <CitedQuote key={i} quote={q.quote}>
                  <span className="mono">{q.doc_id} v{q.version} · {q.section}</span>
                  {q.stale && <span className="flag" data-word>{words.labels.stale}</span>}
                </CitedQuote>
              ))}
            </section>
          </td>
        </tr>
      )}
    </>
  )
}

function Remediation({ report: { remediation, words } }: { report: ReadinessReport }) {
  const { labels } = words
  return (
    <section className="report-part" aria-labelledby="report-remediation">
      <h2 id="report-remediation">{labels.remediation}</h2>
      {remediation.length === 0 ? <p className="report-muted" data-word>{words.sentences.no_remediation}</p>
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

function NotApplicableList({ report: { not_applicable: listed, words } }: { report: ReadinessReport }) {
  return (
    <section className="report-part" aria-labelledby="report-not-applicable">
      <h2 id="report-not-applicable">{words.labels.not_applicable}</h2>
      {listed.length === 0 ? <p className="report-muted" data-word>{words.sentences.no_not_applicable}</p> : (
        <ul className="report-list">
          {listed.map((r) => (
            <li key={`${r.control_id} ${r.requirement_id}`}>
              <span className="mono">{r.control_id} {r.requirement_id}</span> {r.text}
              <dl className="report-reason"><dt>{words.labels.reason}</dt><dd>{r.reason}</dd></dl>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

type Older = { status: 'idle' } | { status: 'reading' } | { status: 'failed'; message: string }

function Trail({ report: { trail, words } }: { report: ReadinessReport }) {
  const { labels } = words
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
  const verdict = (v: Verdict | null) => v ? <span data-word>{words.verdicts[v]}</span> : null
  return (
    <section className="report-part" aria-labelledby="report-trail">
      <h2 id="report-trail">{labels.trail}</h2>
      {entries.length === 0 ? <p className="report-muted" data-word>{words.sentences.no_trail}</p> : (
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
                  <td className="mono report-line">{timeOf(e.at)}</td>
                  <td>{e.reviewer_name}</td>
                  <td className="mono">{e.control_id}</td>
                  <td className="mono">{e.requirement_id ?? ''}</td>
                  <td><span data-word>{words.actions[e.action]}</span></td>
                  <td>{verdict(e.verdict_before)}</td>
                  <td>{verdict(e.verdict_after)}</td>
                  <td className="report-q">{e.note}</td>
                  <td className="mono">
                    {e.evidence.map((d) => <span key={`${d.doc_id} ${d.version}`} className="report-line">{d.doc_id} v{d.version}</span>)}
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
