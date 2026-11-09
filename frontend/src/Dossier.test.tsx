import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Dossier from './Dossier.tsx'
import type { ControlCard, Overview } from './overview.ts'
import type { Verdict } from './assessment.ts'
import type { Goal } from './report.ts'
import type { PassAssessment, PassState } from './profilePass.ts'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** No profile pass: idle, and starting one does nothing. */
const NO_PASS = { pass: { kind: 'idle' } as const, onPass: () => {} }

/** No goal change: one would read the overview again, and say it did. */
const NO_GOAL = { onGoalChanged: () => Promise.resolve(true), onRoundChanged: () => Promise.resolve(true) }

const IDS = ['1.1.1', '1.6.1', '1.6.2', '2.1.3', '4.1.2', '4.1.3', '4.2.1', '5.1.1', '5.1.2', '5.2.4', '5.2.5', '5.2.9']

function seeded(cards: Partial<Record<string, Pick<ControlCard, 'status' | 'verdict'>>> = {}): Overview {
  const controls = IDS.map((id) => ({
    id, question: `Question ${id}?`, iso_27001: 'ISO 27001:2022: A.5.1',
    ...(cards[id] ?? { status: 'not-assessed' as const, verdict: null }),
  }))
  const assessed = Object.keys(cards).length > 0
  return {
    company: 'Nordlicht Komponenten GmbH', country: 'Germany', protection_level: 'normal', answer_language: 'en',
    goal: 'audit-readiness', requirements: {},
    rounds: { current: { name: 'First round', opened_at: null }, comparison: null },
    readiness: { controls: 12, accepted: 2,
      by_verdict: { evidenced: 1, 'partly-evidenced': 1, 'not-evidenced': 0, 'not-applicable': 0 }, open: 10,
      remediation_items: 3, remediation_approved: 0 },
    by_status: { 'not-assessed': 12 - Object.keys(cards).length, 'waiting-for-review': 1, accepted: 2, rejected: 0 },
    next_step: { control_id: assessed ? '4.1.2' : '1.1.1', action: assessed ? 'review' : 'assess' },
    controls,
    quotes: assessed ? [{ control_id: '1.1.1', requirement_id: 'M1', doc_id: 'isms-policy', version: '2.0', section: 'Scope',
      quote: 'The ISMS covers every site.', kind: 'documented', stale: true }] : [],
  }
}

const tile = (label: string) => screen.getByText(label, { selector: '.tile span' }).previousElementSibling?.textContent

/** One card of each verdict. */
const ALL_VERDICTS = {
  '1.1.1': { status: 'accepted', verdict: 'evidenced' },
  '4.1.2': { status: 'waiting-for-review', verdict: 'partly-evidenced' },
  '4.1.3': { status: 'rejected', verdict: 'not-evidenced' },
  '5.2.9': { status: 'accepted', verdict: 'not-applicable' },
} as const

/** The verdict chip on a control's card. */
const cardChip = (id: string) =>
  within(screen.getByRole('article', { name: `Control ${id}` })).getByText(/./, { selector: '.chip' })

describe('the Dossier overview', () => {
  it('shows the readiness band from the overview, in figures and words', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded({ '1.1.1': { status: 'accepted', verdict: 'evidenced' } }) }}
      onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    const band = screen.getByRole('region', { name: 'Readiness' })
    expect(within(band).getByText('accepted of 12')).toBeTruthy()
    expect(tile('accepted of 12')).toBe('2')
    expect(tile('evidenced')).toBe('1')
    expect(tile('partly evidenced')).toBe('1')
    expect(tile('not evidenced')).toBe('0')
    expect(tile('open')).toBe('10')
    expect(tile('remediation items')).toBe('3')
    expect(screen.getByRole('heading', { level: 1 }).parentElement!.textContent).toContain('2 of 12 controls accepted.')
    expect(screen.getByRole('heading', { level: 1 }).parentElement!.textContent).toContain('1 verdict waiting for your review')
  })

  it.each([
    [1, 'remediation items (1 approved)'], // some approved: said, as the report counts only those (#310)
    [0, 'remediation items'], // none: no "(0 approved)"
    [3, 'remediation items'], // all: no "(3 approved)" either
  ])('labels the remediation count with %i approved as "%s"', (approved, label) => {
    const overview = seeded()
    overview.readiness = { ...overview.readiness, remediation_items: 3, remediation_approved: approved }
    render(<Dossier state={{ status: 'ready', overview }} onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(tile(label)).toBe('3')
  })

  it('names the controls by their count, from the data', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(screen.getByRole('heading', { name: '12 controls' })).toBeTruthy()
  })

  it("shows Assessing on the card of the control being assessed, and doesn't offer to assess it again", async () => {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={onOpen} running="1.1.1" {...NO_PASS} {...NO_GOAL} />)
    const card = screen.getByRole('article', { name: 'Control 1.1.1' })
    expect(within(card).getByText('Assessing').className).toBe('chip v-assessing')
    expect(screen.queryByRole('button', { name: 'Assess 1.1.1' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Open 1.1.1' }))
    expect(onOpen).toHaveBeenCalledWith('1.1.1', false)
  })

  it('shows two quotes alike as two cards, without a key clash', () => {
    const overview = seeded({ '1.1.1': { status: 'accepted', verdict: 'evidenced' } })
    overview.quotes = [overview.quotes[0], { ...overview.quotes[0] }]
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<Dossier state={{ status: 'ready', overview }} onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    const quotes = screen.getByRole('region', { name: 'What the evidence says' })
    expect(within(quotes).getAllByRole('article')).toHaveLength(2)
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/)
    errors.mockRestore()
  })

  it('shows a card per control with its review state and verdict', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded({
      '1.1.1': { status: 'accepted', verdict: 'evidenced' },
      '4.1.2': { status: 'waiting-for-review', verdict: 'partly-evidenced' },
      '4.1.3': { status: 'rejected', verdict: 'not-evidenced' },
    }) }} onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    const cards = screen.getAllByRole('article', { name: /^Control / })
    expect(cards).toHaveLength(12)
    const card = (id: string) => screen.getByRole('article', { name: `Control ${id}` })
    expect(within(card('1.1.1')).getByText('Accepted')).toBeTruthy()
    expect(within(card('1.1.1')).getByText('Evidenced').className).toBe('chip v-evidenced')
    expect(within(card('4.1.2')).getByText('Proposed')).toBeTruthy()
    expect(within(card('4.1.3')).getByText('Rejected')).toBeTruthy()
    expect(within(card('5.2.9')).getByText('Not assessed').className).toBe('chip v-none')
  })

  it("shows a German profile's card verdicts in German, marked lang=\"de\" (ticket 200)", () => {
    const overview = { ...seeded(ALL_VERDICTS), company: 'Alpenwerk Präzision GmbH', country: 'Austria',
      answer_language: 'de' }
    render(<Dossier state={{ status: 'ready', overview }} onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(['1.1.1', '4.1.2', '4.1.3', '5.2.9'].map(cardChip).map((c) => [c.textContent, c.lang])).toEqual([
      ['Nachgewiesen', 'de'], ['Teilweise nachgewiesen', 'de'], ['Nicht nachgewiesen', 'de'], ['Nicht anwendbar', 'de']])
    // The interface stays English: the review state and Not assessed.
    expect(within(screen.getByRole('article', { name: 'Control 1.1.1' })).getByText('Accepted')).toBeTruthy()
    expect(cardChip('5.1.1').textContent).toBe('Not assessed')
    expect(cardChip('5.1.1').hasAttribute('lang')).toBe(false)
  })

  it("shows an English profile's card verdicts in English, with no lang (ticket 200)", () => {
    render(<Dossier state={{ status: 'ready', overview: seeded(ALL_VERDICTS) }} onRetry={() => {}} onOpen={() => {}}
      running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(['1.1.1', '4.1.2', '4.1.3', '5.2.9'].map(cardChip).map((c) => [c.textContent, c.hasAttribute('lang')]))
      .toEqual([['Evidenced', false], ['Partly evidenced', false], ['Not evidenced', false], ['Not applicable', false]])
  })

  it('opens a control from its card', async () => {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={onOpen} running={null} {...NO_PASS} {...NO_GOAL} />)
    await userEvent.click(screen.getByRole('link', { name: 'Open 5.2.9' }))
    expect(onOpen).toHaveBeenCalledWith('5.2.9', false)
  })

  it('offers the next step, which assesses a control not yet assessed', async () => {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={onOpen} running={null} {...NO_PASS} {...NO_GOAL} />)
    await userEvent.click(screen.getByRole('button', { name: 'Assess 1.1.1' }))
    expect(onOpen).toHaveBeenCalledWith('1.1.1', true)
  })

  it('shows the evidence quotes with their document, version and section', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded({ '1.1.1': { status: 'accepted', verdict: 'evidenced' } }) }}
      onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    const quotes = screen.getByRole('region', { name: 'What the evidence says' })
    const card = within(quotes).getByRole('article')
    expect(within(card).getByText('The ISMS covers every site.')).toBeTruthy()
    expect(card.textContent).toContain('isms-policy v2.0 · Scope')
    expect(card.textContent).toContain('stale')
  })

  it('with nothing assessed names the first step instead of quotes', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(screen.getByRole('heading', { level: 1 }).parentElement!.textContent)
      .toContain('Nothing is assessed yet. Start with 1.1.1')
    const quotes = screen.getByRole('region', { name: 'What the evidence says' })
    expect(within(quotes).queryAllByRole('article')).toHaveLength(0)
    expect(within(quotes).queryAllByRole('link')).toHaveLength(0)
    expect(quotes.textContent).toContain('No evidence on file yet')
  })

  it("opens a quote's control from the quote card's link, in place and without assessing (ticket 311)", async () => {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: seeded({ '1.1.1': { status: 'accepted', verdict: 'evidenced' } }) }}
      onRetry={() => {}} onOpen={onOpen} running={null} {...NO_PASS} {...NO_GOAL} />)
    const quotes = screen.getByRole('region', { name: 'What the evidence says' })
    const link = within(quotes).getByRole('link', { name: 'for 1.1.1 M1' })
    expect(link.getAttribute('href')).toBe('/controls/1.1.1')
    await userEvent.click(link)
    expect(onOpen).toHaveBeenCalledWith('1.1.1', false)
  })

  it("links two quotes for one control each to it, and leaves the card's Open link the only one so named (ticket 311)",
    () => {
      const overview = seeded({ '1.1.1': { status: 'accepted', verdict: 'evidenced' } })
      overview.quotes = [overview.quotes[0], { ...overview.quotes[0], requirement_id: 'M2', quote: 'Every site.' }]
      render(<Dossier state={{ status: 'ready', overview }} onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
      const quotes = screen.getByRole('region', { name: 'What the evidence says' })
      const links = within(quotes).getAllByRole('link')
      expect(links.map((a) => a.getAttribute('href'))).toEqual(['/controls/1.1.1', '/controls/1.1.1'])
      expect(screen.getAllByRole('link', { name: 'Open 1.1.1' })).toHaveLength(1)
    })

  it('says when the overview could not be loaded, and retries', async () => {
    const onRetry = vi.fn()
    render(<Dossier state={{ status: 'failed', message: 'The overview could not be loaded just now. Please try again.' }}
      onRetry={onRetry} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(screen.getByRole('alert').textContent).toBe('The overview could not be loaded just now. Please try again.')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalled()
  })
})

describe('the profile pass on the Dossier (ticket 390)', () => {
  const NOTE = 'Assesses every open control in one run. Accepted controls are skipped.'
  const passButton = () => screen.getByRole('button', { name: /profile pass/i })
  const panel = () => screen.getByRole('region', { name: 'Profile pass' })
  const row = (id: string) => within(panel()).getByRole('listitem', { name: `Control ${id}` })
  const proposed = (control_id: string, verdict: PassAssessment['verdict']): PassAssessment =>
    ({ thread_id: `t-${control_id}`, control_id, status: 'waiting-for-review', verdict })

  function shown(pass: PassState, { overview = seeded(), running = null as string | null } = {}) {
    const onOpen = vi.fn()
    const onPass = vi.fn()
    render(<Dossier state={{ status: 'ready', overview }} onRetry={() => {}} onOpen={onOpen} running={running}
      pass={pass} onPass={onPass} {...NO_GOAL} />)
    return { onOpen, onPass }
  }

  it('offers Run a profile pass beside the next step while a control is open, with its note', async () => {
    const { onPass, onOpen } = shown({ kind: 'idle' })
    expect(passButton().textContent).toBe('Run a profile pass')
    expect(passButton().className).toBe('btn btn-ghost')
    expect((passButton() as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText(NOTE)).toBeTruthy()
    await userEvent.click(passButton())
    expect(onPass).toHaveBeenCalled()
    expect(onOpen).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: 'Profile pass' })).toBeNull()
  })

  it("isn't offered once every control is accepted", () => {
    const overview = seeded()
    overview.readiness = { ...overview.readiness, open: 0, accepted: 12 }
    overview.next_step = null
    shown({ kind: 'idle' }, { overview })
    expect(screen.queryByRole('button', { name: /profile pass/i })).toBeNull()
    expect(screen.queryByText(NOTE)).toBeNull()
  })

  it('while the pass runs, is busy and disabled, says so, and the next step opens its paper without assessing',
    async () => {
      const { onOpen } = shown({ kind: 'running' })
      expect(passButton().textContent).toBe('Running the profile pass…')
      expect(passButton().getAttribute('aria-busy')).toBe('true')
      expect((passButton() as HTMLButtonElement).disabled).toBe(true)
      expect(screen.getByRole('status').textContent).toBe(
        'Vouch is assessing every open control in one run. This can take a few minutes.')
      expect(screen.queryByRole('button', { name: 'Assess 1.1.1' })).toBeNull()
      await userEvent.click(screen.getByRole('button', { name: 'Open 1.1.1' }))
      expect(onOpen).toHaveBeenCalledWith('1.1.1', false)
    })

  it('while a per-control assessment runs, is disabled and says why', () => {
    shown({ kind: 'idle' }, { running: '1.1.1' })
    expect((passButton() as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Vouch is assessing 1.1.1. One run at a time.')).toBeTruthy()
    expect(screen.queryByText(NOTE)).toBeNull()
  })

  it('when done, counts what waits for review, names the skipped, and reads each row from its card', () => {
    // 1.1.1 was accepted since the pass: its row says so, from the card.
    const overview = seeded({
      '1.1.1': { status: 'accepted', verdict: 'evidenced' },
      '4.1.2': { status: 'waiting-for-review', verdict: 'partly-evidenced' },
      '4.1.3': { status: 'waiting-for-review', verdict: 'not-evidenced' },
      '5.2.9': { status: 'accepted', verdict: 'evidenced' },
    })
    shown({ kind: 'done', skipped: ['5.2.9'], assessments: [
      proposed('1.1.1', 'not-evidenced'), proposed('4.1.2', 'partly-evidenced'), proposed('4.1.3', 'not-evidenced'),
    ] }, { overview })
    expect(panel().className).toContain('sheet')
    expect(panel().textContent).toContain('3 assessments waiting for your review.')
    expect(panel().textContent).toContain('1 accepted control skipped: 5.2.9.')
    expect(within(row('1.1.1')).getByText('Accepted')).toBeTruthy()
    expect(within(row('1.1.1')).getByText('Evidenced').className).toBe('chip v-evidenced')
    expect(within(row('4.1.2')).getByText('Proposed')).toBeTruthy()
    expect(within(row('4.1.2')).getByText('Partly evidenced').className).toBe('chip v-partly-evidenced')
  })

  it('offers Assess <id> again only on not- and partly-evidenced rows, and Open <id> on each', async () => {
    const overview = seeded({
      '1.1.1': { status: 'waiting-for-review', verdict: 'evidenced' },
      '4.1.2': { status: 'waiting-for-review', verdict: 'partly-evidenced' },
      '4.1.3': { status: 'waiting-for-review', verdict: 'not-evidenced' },
      '5.2.9': { status: 'waiting-for-review', verdict: 'not-applicable' },
    })
    const { onOpen } = shown({ kind: 'done', skipped: [], assessments: [
      proposed('1.1.1', 'evidenced'), proposed('4.1.2', 'partly-evidenced'), proposed('4.1.3', 'not-evidenced'),
      proposed('5.2.9', 'not-applicable'),
    ] }, { overview })
    const again = within(panel()).getAllByRole('button', { name: /again$/ })
    expect(again.map((b) => b.textContent)).toEqual(['Assess 4.1.2 again', 'Assess 4.1.3 again'])
    expect(again[0].className).toBe('btn btn-ghost')
    await userEvent.click(again[1])
    expect(onOpen).toHaveBeenLastCalledWith('4.1.3', true)
    await userEvent.click(within(row('1.1.1')).getByRole('button', { name: 'Open 1.1.1' }))
    expect(onOpen).toHaveBeenLastCalledWith('1.1.1', false)
  })

  it('with nothing to assess, says every applicable control is accepted already and names the skipped', () => {
    shown({ kind: 'done', skipped: ['1.1.1', '4.1.2'], assessments: [] })
    expect(panel().textContent).toContain('Every applicable control is accepted already. Nothing was assessed.')
    expect(panel().textContent).toContain('2 accepted controls skipped: 1.1.1, 4.1.2.')
    expect(within(panel()).queryAllByRole('listitem')).toHaveLength(0)
  })

  it.each([
    ['refused', 'The profile pass can\'t run: its prompt is about 90,000 tokens.'],
    ['limit', 'You have started 3 profile passes today. The limit resets at 00:00 UTC.'],
  ] as const)('when %s, gives the API\'s sentence and offers the first control not assessed or rejected on its own',
    async (kind, message) => {
      const overview = seeded({
        '1.1.1': { status: 'accepted', verdict: 'evidenced' },
        '1.6.1': { status: 'waiting-for-review', verdict: 'partly-evidenced' },
        '1.6.2': { status: 'rejected', verdict: 'not-evidenced' },
      })
      const { onOpen } = shown({ kind, message }, { overview })
      expect(panel().textContent).toContain(message)
      const own = within(panel()).getByRole('button', { name: 'Assess 1.6.2 on its own' })
      expect(own.className).toBe('btn btn-primary')
      await userEvent.click(own)
      expect(onOpen).toHaveBeenCalledWith('1.6.2', true)
      expect(within(panel()).queryByRole('button', { name: 'Try again' })).toBeNull()
    })

  it('when refused with no control left to assess on its own, offers no button', () => {
    const overview = seeded(Object.fromEntries(IDS.map((id) =>
      [id, { status: 'waiting-for-review', verdict: 'evidenced' } as const])))
    shown({ kind: 'refused', message: 'Too long.' }, { overview })
    expect(within(panel()).queryAllByRole('button')).toHaveLength(0)
  })

  it.each(['failed', 'unreachable'] as const)('when %s, gives the message and Try again runs the pass again',
    async (kind) => {
      const { onPass } = shown({ kind, message: 'An assessment is already running.' })
      expect(within(panel()).getByRole('alert').textContent).toBe('An assessment is already running.')
      await userEvent.click(within(panel()).getByRole('button', { name: 'Try again' }))
      expect(onPass).toHaveBeenCalled()
    })

  describe('while a per-control assessment runs (one run at a time)', () => {
    const HELD = 'Vouch is assessing 4.1.3. One run at a time.'

    it('holds Try again, saying why', () => {
      const { onPass } = shown({ kind: 'failed', message: 'The profile pass could not be completed.' },
        { running: '4.1.3' })
      const again = within(panel()).getByRole('button', { name: 'Try again' }) as HTMLButtonElement
      expect(again.disabled).toBe(true)
      expect(within(panel()).getByText(HELD)).toBeTruthy()
      expect(onPass).not.toHaveBeenCalled()
    })

    it.each(['refused', 'limit'] as const)('when %s, offers Open <id> instead of assessing it on its own',
      async (kind) => {
        const { onOpen } = shown({ kind, message: 'Too long.' }, { running: '4.1.3' })
        expect(within(panel()).queryByRole('button', { name: /on its own/ })).toBeNull()
        await userEvent.click(within(panel()).getByRole('button', { name: 'Open 1.1.1' }))
        expect(onOpen).toHaveBeenCalledWith('1.1.1', false)
        expect(within(panel()).getByText(HELD)).toBeTruthy()
      })

    it('offers no Assess again on the rows, saying why', () => {
      const overview = seeded({ '4.1.2': { status: 'waiting-for-review', verdict: 'partly-evidenced' } })
      shown({ kind: 'done', skipped: [], assessments: [proposed('4.1.2', 'partly-evidenced')] },
        { overview, running: '4.1.3' })
      expect(within(panel()).queryByRole('button', { name: /again$/ })).toBeNull()
      expect(within(row('4.1.2')).getByRole('button', { name: 'Open 4.1.2' })).toBeTruthy()
      expect(within(panel()).getByText(HELD)).toBeTruthy()
    })
  })
})

describe('the goal on the Dossier (ticket 369)', () => {
  const FAILED = 'The goal could not be changed just now. Please try again.'
  const STALE = 'The goal was changed, but the overview could not be read again just now.'
  const goalLine = () => screen.getByText(/^Goal: /)
  const changeButton = () => screen.getByRole('button', { name: 'Change goal' }) as HTMLButtonElement

  /** The API: PUT /api/goal answers `answer`; nothing else is asked. */
  function serve(answer: () => Promise<Response>) {
    const fetch = vi.fn((url: string, init?: RequestInit) =>
      url === '/api/goal' && init?.method === 'PUT' ? answer() : new Promise<Response>(() => {}))
    vi.stubGlobal('fetch', fetch)
    return fetch
  }

  function shown(overview = seeded(),
    { running = null as string | null, pass = NO_PASS.pass as PassState, read = true } = {}) {
    const onGoalChanged = vi.fn((_goal: Goal) => Promise.resolve(read))
    render(<Dossier state={{ status: 'ready', overview }} onRetry={() => {}} onOpen={() => {}} running={running}
      pass={pass} onPass={() => {}} onGoalChanged={onGoalChanged} onRoundChanged={() => Promise.resolve(true)} />)
    return { onGoalChanged }
  }

  it('shows the goal under the company pill, with Change goal beside it', () => {
    shown()
    expect(goalLine().textContent).toBe('Goal: Audit readiness')
    expect(changeButton().className).toBe('btn btn-ghost')
    const hero = screen.getByRole('heading', { level: 1 }).parentElement!
    const order = [...hero.children].map((el) => el.className || el.tagName)
    expect(order.indexOf('goal')).toBe(order.indexOf('pill') + 1)
  })

  it('names Internal improvement when that is the goal', () => {
    shown({ ...seeded(), goal: 'internal-improvement' })
    expect(goalLine().textContent).toBe('Goal: Internal improvement')
  })

  it('asks first, and on Cancel sends nothing', async () => {
    const fetch = serve(() => Promise.resolve(new Response('{}')))
    const ask = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { onGoalChanged } = shown()
    await userEvent.click(changeButton())
    expect(ask).toHaveBeenCalledWith(
      "Change this profile's goal to Internal improvement? The change is recorded in the audit trail.")
    expect(fetch).not.toHaveBeenCalled()
    expect(onGoalChanged).not.toHaveBeenCalled()
  })

  it('on OK sends the other goal, then reads the overview again', async () => {
    const fetch = serve(() => Promise.resolve(new Response(JSON.stringify({ goal: 'audit-readiness' }))))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onGoalChanged } = shown({ ...seeded(), goal: 'internal-improvement' })
    await userEvent.click(changeButton())
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/goal')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({ goal: 'audit-readiness' })
    expect(onGoalChanged).toHaveBeenCalledTimes(1)
    expect(onGoalChanged).toHaveBeenCalledWith('audit-readiness')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("when the change is kept but the overview can't be read again, says so with Retry, which reads it again",
    async () => {
      serve(() => Promise.resolve(new Response(JSON.stringify({ goal: 'internal-improvement' }))))
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const { onGoalChanged } = shown(seeded(), { read: false })
      await userEvent.click(changeButton())
      expect((await screen.findByRole('alert')).textContent).toBe(STALE)
      expect(onGoalChanged).toHaveBeenCalledWith('internal-improvement')
      onGoalChanged.mockResolvedValue(true)
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
      expect(onGoalChanged).toHaveBeenCalledTimes(2)
      expect(onGoalChanged).toHaveBeenLastCalledWith('internal-improvement')
      await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    })

  it('is busy and disabled while the change runs', async () => {
    let answer: (r: Response) => void = () => {}
    serve(() => new Promise<Response>((resolve) => { answer = resolve }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    shown()
    await userEvent.click(changeButton())
    expect(changeButton().disabled).toBe(true)
    expect(changeButton().getAttribute('aria-busy')).toBe('true')
    answer(new Response(JSON.stringify({ goal: 'internal-improvement' })))
    await waitFor(() => expect(changeButton().disabled).toBe(false))
    expect(changeButton().getAttribute('aria-busy')).toBeNull()
  })

  it.each([
    ['a 503', () => Promise.resolve(new Response('{}', { status: 503 }))],
    ['no answer', () => Promise.reject(new TypeError('offline'))],
  ])('on %s says so and keeps the old goal', async (_, answer) => {
    serve(answer)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onGoalChanged } = shown()
    await userEvent.click(changeButton())
    expect((await screen.findByRole('alert')).textContent).toBe(FAILED)
    expect(goalLine().textContent).toBe('Goal: Audit readiness')
    expect(onGoalChanged).not.toHaveBeenCalled()
    expect(changeButton().disabled).toBe(false)
  })

  it('stays usable while an assessment or a profile pass runs', () => {
    shown(seeded(), { running: '4.1.3' })
    expect(changeButton().disabled).toBe(false)
    cleanup()
    shown(seeded(), { pass: { kind: 'running' } })
    expect(changeButton().disabled).toBe(false)
  })
})

describe('the review round on the Dossier (ticket 402)', () => {
  const FAILED = 'The review round could not be changed just now. Please try again.'
  const roundLine = () => screen.getByText(/^Review round: |^No review round open\./)
  const closeButton = () => screen.getByRole('button', { name: 'Close round' }) as HTMLButtonElement
  const startButton = () => screen.getByRole('button', { name: 'Start round' }) as HTMLButtonElement
  const nameField = () => screen.getByRole('textbox', { name: 'Name of the next round' }) as HTMLInputElement
  const closed = { current: null, comparison: null }

  /** The API: POST to the rounds answers `answer`; nothing else is asked. */
  function serve(answer: () => Promise<Response>) {
    const fetch = vi.fn((url: string, init?: RequestInit) =>
      url.startsWith('/api/rounds') && init?.method === 'POST' ? answer() : new Promise<Response>(() => {}))
    vi.stubGlobal('fetch', fetch)
    return fetch
  }

  function shown(rounds: Overview['rounds'] = seeded().rounds, { read = true } = {}) {
    const onRoundChanged = vi.fn(() => Promise.resolve(read))
    render(<Dossier state={{ status: 'ready', overview: { ...seeded(), rounds } }} onRetry={() => {}}
      onOpen={() => {}} running={null} {...NO_PASS} onGoalChanged={() => Promise.resolve(true)}
      onRoundChanged={onRoundChanged} />)
    return { onRoundChanged }
  }

  it('shows the first round open since the start, under the goal, with a ghost Close round', () => {
    shown()
    expect(roundLine().textContent).toBe('Review round: First round, open since the start')
    expect(closeButton().className).toBe('btn btn-ghost')
    const hero = screen.getByRole('heading', { level: 1 }).parentElement!
    const order = [...hero.children].map((el) => el.className || el.tagName)
    expect(order.indexOf('round')).toBe(order.indexOf('goal') + 1)
  })

  it('names a started round with the day it opened', () => {
    shown({ current: { name: 'Q4 2026', opened_at: '2026-11-09T10:30:00+00:00' }, comparison: null })
    expect(roundLine().textContent).toBe('Review round: Q4 2026, open since 9 November 2026')
  })

  it('asks before closing, and on Cancel sends nothing', async () => {
    const fetch = serve(() => Promise.resolve(new Response('{}')))
    const ask = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { onRoundChanged } = shown({ current: { name: 'Q4 2026', opened_at: '2026-11-09T10:30:00Z' }, comparison: null })
    await userEvent.click(closeButton())
    expect(ask).toHaveBeenCalledWith('Close the review round "Q4 2026"? Its results are frozen, and the overview '
      + 'compares later work with them. The close is recorded in the audit trail.')
    expect(fetch).not.toHaveBeenCalled()
    expect(onRoundChanged).not.toHaveBeenCalled()
  })

  it('on OK closes the round, busy meanwhile, then reads the overview again', async () => {
    let answer: (r: Response) => void = () => {}
    const fetch = serve(() => new Promise<Response>((resolve) => { answer = resolve }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onRoundChanged } = shown()
    await userEvent.click(closeButton())
    expect(fetch.mock.calls[0][0]).toBe('/api/rounds/close')
    expect(closeButton().disabled).toBe(true)
    expect(closeButton().getAttribute('aria-busy')).toBe('true')
    answer(new Response(JSON.stringify({ closed: { name: 'First round', closed_at: '2026-11-09T10:30:00Z' } })))
    await waitFor(() => expect(onRoundChanged).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(closeButton().disabled).toBe(false))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("when the change is kept but the overview can't be read again, says so with Retry", async () => {
    serve(() => Promise.resolve(new Response('{}')))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onRoundChanged } = shown(seeded().rounds, { read: false })
    await userEvent.click(closeButton())
    expect((await screen.findByRole('alert')).textContent)
      .toBe('The review round was changed, but the overview could not be read again just now.')
    onRoundChanged.mockResolvedValue(true)
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRoundChanged).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('with none open, offers a labelled name field and Start round', () => {
    shown(closed)
    expect(roundLine().textContent).toBe('No review round open.')
    expect(nameField().value).toBe('')
    expect(startButton().className).toBe('btn btn-ghost')
    expect(screen.queryByRole('button', { name: 'Close round' })).toBeNull()
  })

  it('refuses a blank name in the browser and sends nothing', async () => {
    const fetch = serve(() => Promise.resolve(new Response('{}')))
    const { onRoundChanged } = shown(closed)
    await userEvent.type(nameField(), '   ')
    await userEvent.click(startButton())
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe('Name the round first.')
    expect(nameField().getAttribute('aria-invalid')).toBe('true')
    expect(nameField().getAttribute('aria-describedby')).toBe(alert.id)
    expect(alert.id).not.toBe('')
    expect(fetch).not.toHaveBeenCalled()
    expect(onRoundChanged).not.toHaveBeenCalled()
  })

  it('caps the name at the API\'s 60 characters', () => {
    shown(closed)
    expect(nameField().maxLength).toBe(60)
    expect(nameField().getAttribute('aria-invalid')).toBeNull()
  })

  it('starts the round by its trimmed name, then reads the overview again', async () => {
    const fetch = serve(() => Promise.resolve(new Response(JSON.stringify({ current: { name: 'Q4 2026' } }))))
    const { onRoundChanged } = shown(closed)
    await userEvent.type(nameField(), ' Q4 2026 ')
    await userEvent.click(startButton())
    await waitFor(() => expect(onRoundChanged).toHaveBeenCalledTimes(1))
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/rounds')
    expect(JSON.parse(String(init?.body))).toEqual({ name: 'Q4 2026' })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it("shows a 409's sentence under the line", async () => {
    const message = 'Close the current review round before starting the next.'
    serve(() => Promise.resolve(new Response(JSON.stringify({ detail: message }), { status: 409 })))
    shown(closed)
    await userEvent.type(nameField(), 'Q4 2026')
    await userEvent.click(startButton())
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(message)
    expect(alert.className).toBe('field-note notice')
  })

  it.each([
    ['a 503', () => Promise.resolve(new Response('{}', { status: 503 }))],
    ['no answer', () => Promise.reject(new TypeError('offline'))],
  ])('on %s says the round could not be changed', async (_, answer) => {
    serve(answer)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onRoundChanged } = shown()
    await userEvent.click(closeButton())
    expect((await screen.findByRole('alert')).textContent).toBe(FAILED)
    expect(onRoundChanged).not.toHaveBeenCalled()
    expect(roundLine().textContent).toBe('Review round: First round, open since the start')
  })
})

describe('the round comparison on the Dossier (ticket 402)', () => {
  const comparison = (changed: { control_id: string; before: Verdict | null; after: Verdict | null }[]) => ({
    against: { name: 'Q4 2026', closed_at: '2026-11-09T10:30:00Z' },
    accepted: { now: 5, then: 3 },
    by_verdict: { evidenced: { now: 4, then: 2 }, 'partly-evidenced': { now: 1, then: 1 },
      'not-evidenced': { now: 0, then: 1 }, 'not-applicable': { now: 0, then: 0 } },
    changed,
  })

  function shown(rounds: Overview['rounds']) {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: { ...seeded(), rounds } }} onRetry={() => {}}
      onOpen={onOpen} running={null} {...NO_PASS} {...NO_GOAL} />)
    return { onOpen, section: screen.getByRole('region', { name: 'Compared with Q4 2026' }) }
  }

  it('shows the counts now with their change, after the readiness band', () => {
    const { section } = shown({ current: null, comparison: comparison([]) })
    expect(within(section).getByText('Since 9 November 2026').className).toBe('pill')
    expect(within(section).getByText('Accepted: 5 now, 3 when Q4 2026 closed.')).toBeTruthy()
    const counts = within(section).getAllByRole('listitem').map((li) => li.textContent)
    expect(counts).toEqual(['Evidenced 4 (+2)', 'Partly evidenced 1 (no change)', 'Not evidenced 0 (-1)'])
    const band = screen.getByRole('region', { name: 'Readiness' })
    expect(band.nextElementSibling).toBe(section)
  })

  it('says when nothing has changed', () => {
    const { section } = shown({ current: null, comparison: comparison([]) })
    expect(within(section).getByText('Nothing has changed since Q4 2026 closed.')).toBeTruthy()
    expect(within(section).queryByText('Changed controls')).toBeNull()
  })

  it('lists each changed control, its link opening the working paper in place', async () => {
    const { section, onOpen } = shown({ current: null, comparison: comparison([
      { control_id: '1.1.1', before: null, after: 'evidenced' },
      { control_id: '4.1.2', before: 'partly-evidenced', after: null },
    ]) })
    expect(within(section).getByRole('heading', { name: 'Changed controls' })).toBeTruthy()
    const links = within(section).getAllByRole('link')
    expect(links.map((a) => a.textContent)).toEqual(
      ['1.1.1: Not accepted → Evidenced', '4.1.2: Partly evidenced → Not accepted'])
    expect(links[0].getAttribute('href')).toBe('/controls/1.1.1')
    await userEvent.click(links[0])
    expect(onOpen).toHaveBeenCalledWith('1.1.1', false)
  })

  it('with no closed round says how to get a comparison', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={() => {}}
      running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(screen.getByText('No closed review round yet. Close this one to compare the next with it.')).toBeTruthy()
  })

  it('with no round open and none closed since the fresh start says to start the next round', () => {
    render(<Dossier state={{ status: 'ready', overview: { ...seeded(), rounds: { current: null, comparison: null } } }}
      onRetry={() => {}} onOpen={() => {}} running={null} {...NO_PASS} {...NO_GOAL} />)
    expect(screen.getByText('No review round has been closed since the fresh start. Start the next round, and once it '
      + 'is closed the overview compares later work with it.')).toBeTruthy()
    expect(screen.queryByText(/Close this one/)).toBeNull()
  })
})
