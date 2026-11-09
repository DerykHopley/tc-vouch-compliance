import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import Report from './Report.tsx'
import { EXPORT_FAILED, OLDER_FAILED, type ReadinessReport as Served, REPORT_FAILED, type ReportWords, type TrailEntry } from './report.ts'
import { SESSION_ENDED, setOnSignedOut } from './session.ts'

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setOnSignedOut(null)
})

/** The English words as `vouch.report.WORDS` serves them. */
const ENGLISH: ReportWords = {
  title: 'Readiness report',
  labels: {
    company: 'Company', country: 'Country', base_framework: 'Base framework', protection_level: 'Protection level',
    overlays: 'Country overlays', answer_language: 'Language', date: 'Drawn up on', readiness: 'Readiness',
    controls: 'Controls', control: 'Control', question: 'Question', status: 'Status', verdict: 'Verdict',
    reviewer: 'Reviewed by', reviewed_at: 'Reviewed on', quotes: 'Quotes', document: 'Document', version: 'Version',
    section: 'Section', stale: 'Stale', remediation: 'Remediation items', remediation_one: 'Remediation item', requirement: 'Requirement',
    missing: 'Missing', owner_role: 'Owner role', due_date: 'Due date', not_applicable: 'Not applicable',
    reason: 'Reason', trail: 'Audit trail', at: 'When', who: 'Who', action: 'Action', note: 'Note',
    verdict_before: 'Verdict before', verdict_after: 'Verdict after', evidence: 'Evidence', model: 'Model',
    prompt_version: 'Prompt version',
  },
  verdicts: { evidenced: 'Evidenced', 'partly-evidenced': 'Partly evidenced', 'not-evidenced': 'Not evidenced',
    'not-applicable': 'Not applicable' },
  statuses: { accepted: 'Accepted', open: 'Open' },
  actions: { accept: 'Accepted', reject: 'Rejected', override: 'Overridden',
    confirm: 'Findings confirmed', approve: 'Approved', discard: 'Discarded', goal: 'Goal changed from {before} to {after}' },
  goals: { 'audit-readiness': 'Audit readiness', 'internal-improvement': 'Internal improvement' },
  protection_levels: { normal: 'Normal', high: 'High', 'very high': 'Very high' },
  languages: { en: 'English', de: 'German' },
  sentences: {
    accepted_of: '{accepted} of {controls} controls accepted',
    no_overlays: 'No country overlay applies to this profile.',
    open_control: 'Open: not yet accepted, so it has no reviewed verdict.',
    no_remediation: 'No remediation item of an accepted control is approved yet.',
    no_not_applicable: 'The profile marks no requirement as not applicable.',
    no_trail: 'The audit trail is empty: no review has been recorded yet.',
    older_entries: 'Older entries are in the audit trail.',
    disclaimer: 'Drafted by an AI assistant, reviewed by {names}; not an audit result.',
    disclaimer_nothing_reviewed: 'Drafted by an AI assistant; nothing has been reviewed yet; not an audit result.',
    and: 'and',
  },
}

const entry = (id: number, at: string, action: TrailEntry['action'], extra: Partial<TrailEntry> = {}): TrailEntry => ({
  id, at, reviewer_name: 'Local user', control_id: '4.1.2', requirement_id: null, action, note: '',
  verdict_before: 'partly-evidenced', verdict_after: 'partly-evidenced',
  evidence: [{ doc_id: 'authentication-standard', version: '3' }], model: 'fake', prompt_version: '7',
  goal_before: null, goal_after: null, ...extra,
})

/** Nordlicht's report with 4.1.2 accepted (one stale quote, one current) and the rest open. */
const REPORT: Served = {
  head: { company: 'Nordlicht Komponenten GmbH', country: 'Germany', base_framework: 'VDA ISA 6 (subset)',
    protection_level: 'high', overlays: [], answer_language: 'en', words_language: 'en', date: '2026-11-06' },
  readiness: { controls: 12, accepted: 1,
    by_verdict: { evidenced: 0, 'partly-evidenced': 1, 'not-evidenced': 0, 'not-applicable': 0 }, open: 11,
    remediation_items: 1, remediation_approved: 1 },
  controls: [
    { id: '4.1.2', question: 'To what extent is access secured?', status: 'accepted', verdict: 'partly-evidenced',
      reviewer: 'Local user', reviewed_at: '2026-11-05T23:30:00+00:00', quotes: [
        { doc_id: 'authentication-standard', version: '3', section: '2 Sign-in', quote: 'Passwords have 12 characters.',
          kind: 'documented', stale: true },
        { doc_id: 'mfa-rollout', version: '1', section: 'Summary', quote: 'Every admin has a key.',
          kind: 'implemented', stale: false },
      ] },
    { id: '5.1.2', question: 'To what extent is information protected?', status: 'open', verdict: null,
      reviewer: null, reviewed_at: null, quotes: [] },
  ],
  remediation: [{ control_id: '4.1.2', question: 'To what extent is access secured?', items: [
    { requirement_id: 'M1', missing: 'No **exposure** record.', owner_role: 'IT security officer',
      due_date: '2026-12-31' },
  ] }],
  not_applicable: [{ control_id: '5.1.2', requirement_id: 'S3', text: 'Records are classified.',
    reason: 'No paper records are kept.' }],
  trail: { entries: [entry(2, '2026-11-06T14:05:09+00:00', 'approve', { requirement_id: 'M1' }),
    entry(1, '2026-11-06T14:04:00+00:00', 'accept')], next: null },
  disclaimer: 'Drafted by an AI assistant, reviewed by Local user; not an audit result.',
  words: ENGLISH,
}

const NOTHING_ACCEPTED: Served = {
  ...REPORT,
  readiness: { controls: 12, accepted: 0,
    by_verdict: { evidenced: 0, 'partly-evidenced': 0, 'not-evidenced': 0, 'not-applicable': 0 }, open: 12,
    remediation_items: 0, remediation_approved: 0 },
  controls: [REPORT.controls[1]],
  remediation: [],
  not_applicable: [],
  trail: { entries: [], next: null },
  disclaimer: ENGLISH.sentences.disclaimer_nothing_reviewed,
}

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }))

/** The API: the report (`report`, each read in turn when a list), the trail's older pages, the export, and nothing
 * else. */
function serve(report: (() => Promise<Response>) | Served, older: (before: string) => Promise<Response> = () =>
  new Promise(() => {}), exported: (url: string) => Promise<Response> = () => new Promise(() => {})) {
  const fetch = vi.fn((url: string) => {
    if (url === '/api/report') return typeof report === 'function' ? report() : json(report)
    if (url === '/api/report/markdown' || url === '/api/report/pdf') return exported(url)
    const before = /^\/api\/audit-trail\?before=(\d+)$/.exec(url)
    if (before) return older(before[1])
    return new Promise<Response>(() => {})
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const reportReads = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.filter(([url]) => url === '/api/report').length
const overviewReads = (fetch: ReturnType<typeof serve>) =>
  fetch.mock.calls.filter(([url]) => url === '/api/overview').length

async function shown() {
  return screen.findByRole('article', { name: ENGLISH.title })
}

describe('The readiness report', () => {
  it("shows an accepted control's quotes with document, version and section, flagging only the stale one", async () => {
    serve(REPORT)
    render(<Report />)
    await shown()

    const quotes = screen.getByRole('region', { name: `${ENGLISH.labels.quotes} 4.1.2` })
    const [stale, current] = within(quotes).getAllByRole('figure')
    expect(within(stale).getByText('Passwords have 12 characters.').tagName).toBe('BLOCKQUOTE')
    expect(within(stale).getByText(/authentication-standard v3 · 2 Sign-in/)).toBeTruthy()
    expect(within(stale).getByText('Stale')).toBeTruthy()
    expect(within(current).getByText(/mfa-rollout v1 · Summary/)).toBeTruthy()
    expect(within(current).queryByText('Stale')).toBeNull()
  })

  it('shows the head, readiness, controls, items, not applicable list, trail and disclaimer as served', async () => {
    serve(REPORT)
    render(<Report />)
    const report = await shown()

    expect(report.getAttribute('lang')).toBe('en')
    expect(within(report).getByText('High')).toBeTruthy() // the protection level in words
    expect(within(report).getByText('No country overlay applies to this profile.')).toBeTruthy()
    expect(within(report).getByText('2026-11-06', { selector: 'dd' })).toBeTruthy()
    expect(within(report).getAllByText('1 of 12 controls accepted').length).toBeGreaterThan(0)
    expect(within(report).getByText('11 Open')).toBeTruthy()
    expect(within(report).getByText('1 Remediation item')).toBeTruthy() // the singular for one

    const controls = within(report).getByRole('table', { name: 'Controls' })
    const accepted = within(controls).getByRole('row', { name: /^4\.1\.2/ })
    expect(within(accepted).getByText('Partly evidenced')).toBeTruthy()
    expect(within(accepted).getByText('Local user')).toBeTruthy()
    expect(within(accepted).getByText('2026-11-05')).toBeTruthy() // its day, in UTC
    const open = within(controls).getByRole('row', { name: /^5\.1\.2/ })
    expect(within(open).getByText('Open')).toBeTruthy()
    expect(within(open).getByText(ENGLISH.sentences.open_control)).toBeTruthy()
    expect(within(open).queryByText('Local user')).toBeNull()

    const items = within(report).getByRole('region', { name: 'Remediation items' })
    expect(within(items).getByText('exposure').tagName).toBe('STRONG') // the model's text, through Prose
    expect(within(items).getByText('IT security officer')).toBeTruthy()
    expect(within(items).getByText('2026-12-31')).toBeTruthy()

    const excluded = within(report).getByRole('region', { name: 'Not applicable' })
    expect(within(excluded).getByText('No paper records are kept.')).toBeTruthy()

    const trail = within(report).getByRole('table', { name: 'Audit trail' })
    const [, newest, older] = within(trail).getAllByRole('row')
    expect(within(newest).getByText('2026-11-06 14:05 UTC')).toBeTruthy()
    expect(within(newest).getByText('Approved')).toBeTruthy()
    expect(within(newest).getByText('authentication-standard v3')).toBeTruthy()
    expect(within(older).getByText('Accepted')).toBeTruthy()

    expect(within(report).getByText(REPORT.disclaimer)).toBeTruthy()
  })

  it('with nothing accepted, says so in the served sentences', async () => {
    serve(NOTHING_ACCEPTED)
    render(<Report />)
    const report = await shown()

    expect(within(report).getAllByText('0 of 12 controls accepted').length).toBeGreaterThan(0)
    for (const sentence of ['no_remediation', 'no_not_applicable', 'no_trail', 'disclaimer_nothing_reviewed']) {
      expect(within(report).getByText(ENGLISH.sentences[sentence])).toBeTruthy()
    }
  })

  it("carries the words' language, English for an answer language without its own words", async () => {
    serve({ ...REPORT, head: { ...REPORT.head, answer_language: 'fr', words_language: 'en' } })
    render(<Report />)
    const report = await shown()

    expect(report.getAttribute('lang')).toBe('en')
    expect(within(report).getByText('fr', { selector: 'dd' })).toBeTruthy() // no word for French: its code
  })

  it('shows a time that does not parse as written, rather than failing', async () => {
    const [accepted, open] = REPORT.controls
    serve({ ...REPORT, controls: [{ ...accepted, reviewed_at: 'not a time' }, open],
      trail: { entries: [entry(1, 'yesterday', 'accept')], next: null } })
    render(<Report />)
    const report = await shown()

    expect(within(report).getByText('not a time')).toBeTruthy()
    expect(within(report).getByText('yesterday')).toBeTruthy()
  })

  it('shows each heading, column name, status, action and sentence in the served words only', async () => {
    // Made-up words: each one marked, so a word of the page's own shows up unmarked.
    const mark = (words: unknown): unknown => typeof words === 'string' ? `~${words}~`
      : Object.fromEntries(Object.entries(words as object).map(([k, v]) => [k, mark(v)]))
    const words = mark(ENGLISH) as ReportWords
    serve({ ...REPORT, head: { ...REPORT.head, answer_language: 'xx', words_language: 'xx' }, words,
      trail: { ...REPORT.trail, entries: [...REPORT.trail.entries, entry(0, '2026-11-06T14:00:00+00:00', 'reject')] } })
    render(<Report />)
    const report = await screen.findByRole('article', { name: words.title })

    const served = new Set<string>()
    const collect = (w: unknown) => typeof w === 'string' ? served.add(w) : Object.values(w as object).forEach(collect)
    collect(words)
    served.add('~1 of 12 controls accepted~') // `accepted_of`, filled in
    const own = [...report.querySelectorAll('h1, h2, h3, th[scope=col], dt, [data-word], .chip')]
      .map((el) => el.textContent ?? '')
    expect(own.length).toBeGreaterThan(30)
    expect(own.filter((text) => !served.has(text))).toEqual([])
    // The statuses, actions and sentences shown, each a served word.
    expect(within(report).getByText(words.verdicts['partly-evidenced'], { selector: '.chip' })).toBeTruthy()
    for (const word of [words.statuses.accepted, words.statuses.open, words.actions.approve, words.actions.accept,
      words.actions.reject, words.sentences.open_control, '~1 of 12 controls accepted~']) {
      expect(within(report).getAllByText(word).length).toBeGreaterThan(0)
    }
  })
})

describe('A goal change in the trail (ticket 369)', () => {
  /** A goal change's entry, as `vouch.goal.goal_entry` writes it: no control, verdict, evidence, model or prompt. */
  const goalChange = entry(3, '2026-11-06T14:10:00+00:00', 'goal', { control_id: '', verdict_before: null,
    verdict_after: null, evidence: [], model: '', prompt_version: '', goal_before: 'audit-readiness',
    goal_after: 'internal-improvement' })

  it('shows the change filled in as its action, its control cells empty', async () => {
    serve({ ...REPORT, trail: { entries: [goalChange, ...REPORT.trail.entries], next: null } })
    render(<Report />)
    const report = await shown()

    const trail = within(report).getByRole('table', { name: 'Audit trail' })
    const [, change] = within(trail).getAllByRole('row')
    const cells = within(change).getAllByRole('cell').map((c) => c.textContent)
    expect(cells).toEqual(['2026-11-06 14:10 UTC', 'Local user', '', '',
      'Goal changed from Audit readiness to Internal improvement', '', '', '', '', '', ''])
  })

  it('fills it from the served words', async () => {
    const words: ReportWords = { ...ENGLISH, actions: { ...ENGLISH.actions, goal: 'Ziel geändert von {before} zu {after}' },
      goals: { 'audit-readiness': 'Audit-Bereitschaft', 'internal-improvement': 'Interner Verbesserung' } }
    serve({ ...REPORT, words, trail: { entries: [goalChange], next: null } })
    render(<Report />)
    const report = await shown()

    expect(within(report).getByText('Ziel geändert von Audit-Bereitschaft zu Interner Verbesserung')).toBeTruthy()
  })
})

describe('Older trail entries', () => {
  const withNext: Served = { ...REPORT, trail: { ...REPORT.trail, next: 1 } }

  it('adds the next page from the trail, and the button goes when there are no more', async () => {
    serve(withNext, (before) => before === '1'
      ? json({ entries: [entry(0, '2026-11-01T09:00:00+00:00', 'reject', { control_id: '1.1.1' })], next: null })
      : json({}, 404))
    render(<Report />)
    await shown()
    const trail = screen.getByRole('table', { name: 'Audit trail' })
    expect(within(trail).getAllByRole('row')).toHaveLength(3)

    await userEvent.click(screen.getByRole('button', { name: 'Show older entries' }))

    await waitFor(() => expect(within(trail).getAllByRole('row')).toHaveLength(4))
    expect(within(trail).getByText('2026-11-01 09:00 UTC')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Show older entries' })).toBeNull()
  })

  it('says so when a page fails, keeping the entries shown and the button', async () => {
    serve(withNext, () => json({}, 503))
    render(<Report />)
    await shown()

    await userEvent.click(screen.getByRole('button', { name: 'Show older entries' }))

    expect(await screen.findByText(OLDER_FAILED)).toBeTruthy()
    expect(within(screen.getByRole('table', { name: 'Audit trail' })).getAllByRole('row')).toHaveLength(3)
    expect(screen.getByRole('button', { name: 'Show older entries' })).toBeTruthy()
  })

  it('is busy while the page is read', async () => {
    serve(withNext) // the page never answers
    render(<Report />)
    await shown()
    const button = screen.getByRole('button', { name: 'Show older entries' })

    await userEvent.click(button)

    expect(button.getAttribute('aria-busy')).toBe('true')
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('The older entries sentence, for print', () => {
  it('is rendered, hidden on screen, when the trail has more entries', async () => {
    serve({ ...REPORT, trail: { ...REPORT.trail, next: 1 } })
    render(<Report />)
    const report = await shown()

    const sentence = within(report).getByText(ENGLISH.sentences.older_entries)
    expect(sentence.classList.contains('print-only')).toBe(true) // app.css: display none but in print
    expect(sentence.closest('[lang]')?.getAttribute('lang')).toBe('en') // the report's words, not the page's
  })

  it('is not rendered when there are no more entries', async () => {
    serve(REPORT)
    render(<Report />)
    const report = await shown()

    expect(within(report).queryByText(ENGLISH.sentences.older_entries)).toBeNull()
  })
})

describe('Export', () => {
  const markdownButton = () => screen.getByRole('button', { name: 'Export Markdown' })
  const pdfButton = () => screen.getByRole('button', { name: 'Export PDF' })
  const disabled = (button: HTMLElement) => (button as HTMLButtonElement).disabled

  it('sits above the report as two buttons, outside its language', async () => {
    serve({ ...REPORT, head: { ...REPORT.head, words_language: 'de' } })
    render(<Report />)
    const report = await shown()

    for (const button of [markdownButton(), pdfButton()]) {
      expect(report.contains(button)).toBe(false)
      expect(button.closest('[lang]')).toBeNull()
      expect(button.closest('.report-actions')).toBeTruthy()
      expect(button.compareDocumentPosition(report) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(button.getAttribute('aria-busy')).toBeNull()
      expect(disabled(button)).toBe(false)
    }
  })

  it.each([
    ['Export PDF', '/api/report/pdf'],
    ['Export Markdown', '/api/report/markdown'],
  ])('while %s runs, it is busy and both buttons are disabled', async (name, url) => {
    const fetch = serve(REPORT) // the export never answers
    render(<Report />)
    await shown()

    await userEvent.click(screen.getByRole('button', { name }))

    const pressed = screen.getByRole('button', { name })
    const other = name === 'Export PDF' ? markdownButton() : pdfButton()
    expect(pressed.getAttribute('aria-busy')).toBe('true')
    expect(other.getAttribute('aria-busy')).toBeNull()
    expect(disabled(pressed)).toBe(true)
    expect(disabled(other)).toBe(true)
    expect(fetch.mock.calls.filter(([u]) => u === url)).toHaveLength(1)
  })

  it('on a failure says so under the buttons, keeps the report, and either can be pressed again', async () => {
    const tries: string[] = []
    const fetch = serve(REPORT, undefined, (url) => { tries.push(url); return json({ detail: 'down' }, 503) })
    render(<Report />)
    await shown()

    await userEvent.click(pdfButton())

    expect((await screen.findByRole('alert')).textContent).toBe(EXPORT_FAILED)
    expect(screen.getByRole('alert').closest('.report-actions')).toBeTruthy()
    expect(screen.getByRole('article', { name: ENGLISH.title })).toBeTruthy()
    for (const button of [markdownButton(), pdfButton()]) {
      expect(disabled(button)).toBe(false)
      expect(button.getAttribute('aria-busy')).toBeNull()
    }

    await userEvent.click(markdownButton())
    await waitFor(() => expect(tries).toEqual(['/api/report/pdf', '/api/report/markdown']))
    expect(reportReads(fetch)).toBe(1) // the report is not read again
  })

  it('on a 401 says the sign-in has ended', async () => {
    const ended = vi.fn()
    setOnSignedOut(ended)
    serve(REPORT, undefined, () => json({}, 401))
    render(<Report />)
    await shown()

    await userEvent.click(pdfButton())

    expect((await screen.findByRole('alert')).textContent).toBe(SESSION_ENDED)
    expect(ended).toHaveBeenCalled()
  })

  it('clears an earlier failure once an export succeeds', async () => {
    let tries = 0
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    serve(REPORT, undefined, () => ++tries === 1 ? json({}, 503)
      : Promise.resolve(new Response('# Readiness report\n', { status: 200 })))
    render(<Report />)
    await shown()

    await userEvent.click(markdownButton())
    await screen.findByRole('alert')
    await userEvent.click(markdownButton())

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('A report that cannot be read', () => {
  it.each([
    ['a network error', () => Promise.reject(new TypeError('Failed to fetch'))],
    ['a 503', () => json({ detail: 'The database is unavailable.' }, 503)],
  ])('on %s shows the formal message and Try again, none of the report, and reads it again', async (_, failing) => {
    let reads = 0
    const fetch = serve(() => ++reads === 1 ? failing() : json(REPORT))
    render(<Report />)

    expect(await screen.findByText(REPORT_FAILED)).toBeTruthy()
    expect(screen.queryByRole('article')).toBeNull()
    expect(screen.queryByText(ENGLISH.title)).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await shown()
    expect(reportReads(fetch)).toBe(2)
    expect(screen.queryByText(REPORT_FAILED)).toBeNull()
  })

  it('on a 401 says the sign-in has ended', async () => {
    const ended = vi.fn()
    setOnSignedOut(ended)
    serve(() => json({}, 401))
    render(<Report />)

    expect(await screen.findByText(SESSION_ENDED)).toBeTruthy()
    expect(ended).toHaveBeenCalled()
  })
})

describe('The Report tab', () => {
  it('opens the report at /report, marked as the current page', async () => {
    const fetch = serve(REPORT)
    render(<App />)
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Overview', 'Report'])

    await userEvent.click(within(nav).getByRole('link', { name: 'Report' }))

    await shown()
    expect(window.location.pathname).toBe('/report')
    expect(within(nav).getByRole('link', { name: 'Report' }).getAttribute('aria-current')).toBe('page')
    expect(within(nav).getByRole('link', { name: 'Overview' }).getAttribute('aria-current')).toBeNull()
    expect(reportReads(fetch)).toBe(1)
  })

  it('opens the report when /report is opened directly, and reads it afresh each time the page opens', async () => {
    window.history.replaceState(null, '', '/report')
    const fetch = serve(REPORT)
    render(<App />)

    await shown()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Report' }).getAttribute('aria-current')).toBe('page')
    expect(overviewReads(fetch)).toBe(0) // the report shows none of it

    await userEvent.click(within(nav).getByRole('link', { name: 'Overview' }))
    expect(overviewReads(fetch)).toBe(1)
    await userEvent.click(within(nav).getByRole('link', { name: 'Report' }))
    await shown()
    expect(reportReads(fetch)).toBe(2)
  })
})
