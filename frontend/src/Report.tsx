import { useEffect, useState } from 'react'
import type { Verdict } from './assessment.ts'
import CitedQuote from './CitedQuote.tsx'
import { SEGMENTS } from './overview.ts'
import Prose from './Prose.tsx'
import { actionWord, type AuditReadinessReport, exportReport, fetchReport, type ImprovementReport, olderEntries,
  type QuestionState, type ReadinessReport, type ReportControl, type ReportFormat, type ReportQuestion, type ReportWords, type TrailEntry }
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
  return <main className="report wrap"><Export /><Document report={state.report} /></main>
}

type Exporting = { status: 'idle' } | { status: 'exporting'; format: ReportFormat } | { status: 'failed'; message: string }

const FORMATS: { format: ReportFormat; label: string }[] = [
  { format: 'markdown', label: 'Export Markdown' },
  { format: 'pdf', label: 'Export PDF' },
]

/** Export (#57, #308): the report as one Markdown or PDF file, saved only once the whole of it has been read. While
 * one runs, its button is busy and both are disabled. English, as the page around the report is; app.css leaves it
 * out of print. */
function Export() {
  const [state, setState] = useState<Exporting>({ status: 'idle' })
  const busy = state.status === 'exporting'
  const run = async (format: ReportFormat) => {
    if (busy) return
    setState({ status: 'exporting', format })
    const done = await exportReport(format)
    setState(done.ok ? { status: 'idle' } : { status: 'failed', message: done.message })
  }
  return (
    <div className="report-actions">
      {FORMATS.map(({ format, label }) => (
        <button key={format} className="btn" onClick={() => void run(format)} disabled={busy}
          aria-busy={busy && state.format === format ? true : undefined}>
          {label}
        </button>
      ))}
      {state.status === 'failed' && <p role="alert" className="field-note notice">{state.message}</p>}
    </div>
  )
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
  const improvement = report.goal === 'internal-improvement'
  return (
    <article className="sheet report-doc" lang={head.words_language} aria-labelledby="report-title">
      <header className="report-top">
        <h1 id="report-title">{improvement ? words.improvement_title : words.title}</h1>
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
      {report.goal === 'internal-improvement' ? (
        <>
          <Comparison report={report} />
          <Plan report={report} />
          <Questions report={report} />
        </>
      ) : (
        <>
          <Controls report={report} />
          <Remediation report={report} />
          <NotApplicableList report={report} />
        </>
      )}
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
    ['remediation', `${r.remediation_items} ${r.remediation_items === 1 ? words.labels.remediation_one : words.labels.remediation}`],
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

/** A count now against then, as `vouch.report.comparison_lines` writes it: "(+2)", "(-1)" or the served no-change
 * word. */
const change = (now: number, then: number, words: ReportWords) =>
  now === then ? words.sentences.no_change : `(${now > then ? '+' : '-'}${Math.abs(now - then)})`

/** The improvement report's comparison (#420): now against the last review round closed since the fresh start,
 * accepted verdicts only, as the overview's (#402); its lines styled as readiness's counts. */
function Comparison({ report: { comparison: c, words } }: { report: ImprovementReport }) {
  const { labels, sentences } = words
  const accepted = (v: Verdict | null) => v ? <VerdictChip verdict={v} label={words.verdicts[v]} />
    : <span data-word>{sentences.not_accepted}</span>
  return (
    <section className="report-part" aria-labelledby="report-comparison">
      <h2 id="report-comparison">{labels.comparison}</h2>
      {c === null ? <p className="report-muted" data-word>{sentences.no_comparison}</p> : (
        <>
          <p className="report-lead">
            {filled(sentences.compared_with, { name: c.against.name, date: dayOf(c.against.closed_at) })}
          </p>
          <p className="report-muted">
            {filled(sentences.accepted_now_then, { now: c.accepted.now, then: c.accepted.then, name: c.against.name })}
          </p>
          <ul className="report-counts">
            {SEGMENTS.filter((v) => c.by_verdict[v].now > 0 || c.by_verdict[v].then > 0).map((v) => (
              <li key={v}>{words.verdicts[v]} {c.by_verdict[v].now} {change(c.by_verdict[v].now, c.by_verdict[v].then, words)}</li>
            ))}
          </ul>
          {c.changed.length === 0 ? <p className="report-muted">{filled(sentences.nothing_changed, { name: c.against.name })}</p> : (
            <div className="report-group">
              <h3 id="report-changed" className="report-group-head">{labels.changed}</h3>
              <div className="table-wrap">
                <table className="report-table" aria-labelledby="report-changed">
                  <thead>
                    <tr>
                      <th scope="col">{labels.control}</th>
                      <th scope="col">{labels.before}</th>
                      <th scope="col">{labels.after}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.changed.map((x) => (
                      <tr key={x.control_id}>
                        <th scope="row" className="mono">{x.control_id}</th>
                        <td>{accepted(x.before)}</td>
                        <td>{accepted(x.after)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}

/** The improvement plan (#420): a table per priority, High, then Medium, then Low, each item with its status and done
 * note. */
function Plan({ report: { plan, words } }: { report: ImprovementReport }) {
  const { labels } = words
  return (
    <section className="report-part" aria-labelledby="report-plan">
      <h2 id="report-plan">{labels.plan}</h2>
      {plan.length === 0 ? <p className="report-muted" data-word>{words.sentences.no_plan}</p>
        : plan.map((group) => (
          <div key={group.priority} className="report-group">
            <h3 id={`report-plan-${group.priority}`} className="report-group-head">{words.priorities[group.priority]}</h3>
            <div className="table-wrap">
              <table className="report-table report-plan" aria-labelledby={`report-plan-${group.priority}`}>
                <thead>
                  <tr>
                    <th scope="col">{labels.control}</th>
                    <th scope="col">{labels.requirement}</th>
                    <th scope="col">{labels.missing}</th>
                    <th scope="col">{labels.owner_role}</th>
                    <th scope="col">{labels.due_date}</th>
                    <th scope="col">{labels.status}</th>
                    <th scope="col">{labels.note}</th>
                  </tr>
                </thead>
                <tbody>
                  {group.items.map((item) => (
                    <tr key={`${item.control_id} ${item.requirement_id}`}>
                      <th scope="row" className="mono">{item.control_id}</th>
                      <td className="mono">{item.requirement_id}</td>
                      <td className="report-q"><Prose text={item.missing} /></td>
                      <td>{item.owner_role}</td>
                      <td className="mono">{item.due_date}</td>
                      <td><span data-word>{words.item_statuses[item.status]}</span></td>
                      <td className="report-q">{item.note}</td>
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

/** A question's status, as `vouch.report.question_status` has it. */
const questionStatus = (q: ReportQuestion): QuestionState => q.answer !== null ? 'answered' : q.overdue ? 'overdue' : 'open'

/** The open questions (#420), in the base framework's control order, oldest first within a control. */
function Questions({ report: { questions, words } }: { report: ImprovementReport }) {
  const { labels } = words
  return (
    <section className="report-part" aria-labelledby="report-questions">
      <h2 id="report-questions">{labels.open_questions}</h2>
      {questions.length === 0 ? <p className="report-muted" data-word>{words.sentences.no_questions}</p> : (
        <div className="table-wrap">
          <table className="report-table report-questions" aria-labelledby="report-questions">
            <thead>
              <tr>
                <th scope="col">{labels.control}</th>
                <th scope="col">{labels.question}</th>
                <th scope="col">{labels.asked_of}</th>
                <th scope="col">{labels.due_date}</th>
                <th scope="col">{labels.status}</th>
                <th scope="col">{labels.answer}</th>
              </tr>
            </thead>
            <tbody>
              {questions.map((q) => (
                <tr key={q.id}>
                  <th scope="row" className="mono">{q.control_id}</th>
                  <td className="report-q">{q.question}</td>
                  <td>{q.asked_of}</td>
                  <td className="mono">{q.due_date}</td>
                  <td><span data-word>{words.question_statuses[questionStatus(q)]}</span></td>
                  <td className="report-q">{q.answer ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Controls({ report: { controls, words } }: { report: AuditReadinessReport }) {
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

function Remediation({ report: { remediation, words } }: { report: AuditReadinessReport }) {
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

function NotApplicableList({ report: { not_applicable: listed, words } }: { report: AuditReadinessReport }) {
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
                  <td className="mono"><span className="report-line">{timeOf(e.at)}</span></td>
                  <td>{e.reviewer_name}</td>
                  <td className="mono">{e.control_id}</td>
                  <td className="mono">{e.requirement_id ?? ''}</td>
                  <td><span data-word>{actionWord(e, words)}</span></td>
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
      {/* Print shows this in place of the button below, which it hides (#57); the screen hides it (#56). */}
      {next !== null && <p className="report-muted print-only" data-word>{words.sentences.older_entries}</p>}
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
