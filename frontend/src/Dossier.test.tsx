import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Dossier from './Dossier.tsx'
import type { ControlCard, Overview } from './overview.ts'

afterEach(cleanup)

const IDS = ['1.1.1', '1.6.1', '1.6.2', '2.1.3', '4.1.2', '4.1.3', '4.2.1', '5.1.1', '5.1.2', '5.2.4', '5.2.5', '5.2.9']

function seeded(cards: Partial<Record<string, Pick<ControlCard, 'status' | 'verdict'>>> = {}): Overview {
  const controls = IDS.map((id) => ({
    id, question: `Question ${id}?`, iso_27001: 'ISO 27001:2022: A.5.1',
    ...(cards[id] ?? { status: 'not-assessed' as const, verdict: null }),
  }))
  const assessed = Object.keys(cards).length > 0
  return {
    company: 'Nordlicht Komponenten GmbH', country: 'Germany',
    readiness: { controls: 12, accepted: 2,
      by_verdict: { evidenced: 1, 'partly-evidenced': 1, 'not-evidenced': 0, 'not-applicable': 0 }, open: 10,
      remediation_items: 3 },
    by_status: { 'not-assessed': 12 - Object.keys(cards).length, 'waiting-for-review': 1, accepted: 2, rejected: 0 },
    next_step: { control_id: assessed ? '4.1.2' : '1.1.1', action: assessed ? 'review' : 'assess' },
    controls,
    quotes: assessed ? [{ control_id: '1.1.1', requirement_id: 'M1', doc_id: 'isms-policy', version: '2.0', section: 'Scope',
      quote: 'The ISMS covers every site.', kind: 'documented', stale: true }] : [],
  }
}

const tile = (label: string) => screen.getByText(label, { selector: '.tile span' }).previousElementSibling?.textContent

describe('the Dossier overview', () => {
  it('shows the readiness band from the overview, in figures and words', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded({ '1.1.1': { status: 'accepted', verdict: 'evidenced' } }) }}
      onRetry={() => {}} onOpen={() => {}} running={null} />)
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

  it('names the controls by their count, from the data', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={() => {}} running={null} />)
    expect(screen.getByRole('heading', { name: '12 controls' })).toBeTruthy()
  })

  it("shows Assessing on the card of the control being assessed, and doesn't offer to assess it again", async () => {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={onOpen} running="1.1.1" />)
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
    render(<Dossier state={{ status: 'ready', overview }} onRetry={() => {}} onOpen={() => {}} running={null} />)
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
    }) }} onRetry={() => {}} onOpen={() => {}} running={null} />)
    const cards = screen.getAllByRole('article', { name: /^Control / })
    expect(cards).toHaveLength(12)
    const card = (id: string) => screen.getByRole('article', { name: `Control ${id}` })
    expect(within(card('1.1.1')).getByText('Accepted')).toBeTruthy()
    expect(within(card('1.1.1')).getByText('Evidenced').className).toBe('chip v-evidenced')
    expect(within(card('4.1.2')).getByText('Proposed')).toBeTruthy()
    expect(within(card('4.1.3')).getByText('Rejected')).toBeTruthy()
    expect(within(card('5.2.9')).getByText('Not assessed').className).toBe('chip v-none')
  })

  it('opens a control from its card', async () => {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={onOpen} running={null} />)
    await userEvent.click(screen.getByRole('link', { name: 'Open 5.2.9' }))
    expect(onOpen).toHaveBeenCalledWith('5.2.9', false)
  })

  it('offers the next step, which assesses a control not yet assessed', async () => {
    const onOpen = vi.fn()
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={onOpen} running={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Assess 1.1.1' }))
    expect(onOpen).toHaveBeenCalledWith('1.1.1', true)
  })

  it('shows the evidence quotes with their document, version and section', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded({ '1.1.1': { status: 'accepted', verdict: 'evidenced' } }) }}
      onRetry={() => {}} onOpen={() => {}} running={null} />)
    const quotes = screen.getByRole('region', { name: 'What the evidence says' })
    const card = within(quotes).getByRole('article')
    expect(within(card).getByText('The ISMS covers every site.')).toBeTruthy()
    expect(card.textContent).toContain('isms-policy v2.0 · Scope')
    expect(card.textContent).toContain('stale')
  })

  it('with nothing assessed names the first step instead of quotes', () => {
    render(<Dossier state={{ status: 'ready', overview: seeded() }} onRetry={() => {}} onOpen={() => {}} running={null} />)
    expect(screen.getByRole('heading', { level: 1 }).parentElement!.textContent)
      .toContain('Nothing is assessed yet. Start with 1.1.1')
    const quotes = screen.getByRole('region', { name: 'What the evidence says' })
    expect(within(quotes).queryAllByRole('article')).toHaveLength(0)
    expect(quotes.textContent).toContain('No evidence on file yet')
  })

  it('says when the overview could not be loaded, and retries', async () => {
    const onRetry = vi.fn()
    render(<Dossier state={{ status: 'failed', message: 'The overview could not be loaded just now. Please try again.' }}
      onRetry={onRetry} onOpen={() => {}} running={null} />)
    expect(screen.getByRole('alert').textContent).toBe('The overview could not be loaded just now. Please try again.')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalled()
  })
})
