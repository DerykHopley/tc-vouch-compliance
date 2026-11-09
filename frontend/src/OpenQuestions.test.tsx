import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Assessment } from './assessment.ts'
import type { ControlCard } from './overview.ts'
import type { OpenQuestion } from './questions.ts'
import type { Goal } from './report.ts'
import WorkingPaper from './WorkingPaper.tsx'

/** The open questions on a control's working paper (#414), through the paper as App shows it, the API mocked. */

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const CARD: ControlCard = { id: '1.1.1', question: 'To what extent are policies defined?', iso_27001: 'ISO 27001:2022: A.5.1',
  status: 'not-assessed', verdict: null }

const ASKED: OpenQuestion = { id: 7, question: 'Who reviews the ISMS scope each year?', asked_of: 'IT lead',
  due_date: '2026-11-30', asked_at: '2026-11-09T09:00:00+00:00', answer: null, answered_at: null, overdue: false }
const ANSWERED: OpenQuestion = { ...ASKED, answer: 'The CISO, each March.', answered_at: '2026-11-10T09:00:00+00:00' }

function paper({ questions = [], goal = 'audit-readiness', onQuestionsChanged = () => {} }:
  { questions?: OpenQuestion[]; goal?: Goal; onQuestionsChanged?: () => void } = {}) {
  render(<WorkingPaper overview={{ company: 'Nordlicht Komponenten GmbH', protection_level: 'normal', answer_language: 'en',
    requirements: {}, controls: [CARD], goal, questions: { '1.1.1': questions } }} card={CARD} running={null}
    run={{ status: 'idle', steps: [] }} onAssess={() => {}} onOpen={() => {}} onOverview={() => {}} onReviewed={() => {}}
    onRefresh={() => Promise.resolve(true)} onItemDecided={() => {}} onQuestionsChanged={onQuestionsChanged} />)
}

/** The questions' routes answering `status` with `body`; what they got. */
function route(status = 200, body: unknown = {}) {
  const sent: { url: string; method?: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    sent.push({ url, method: init?.method, body: JSON.parse(init!.body as string) })
    return Promise.resolve(Response.json(body, { status }))
  }))
  return sent
}

const section = () => screen.getByRole('region', { name: 'Open questions' })
const form = () => within(section()).getByRole('form', { name: 'Log a question on 1.1.1' })

async function fill({ question = 'Who reviews the ISMS scope each year?', askedOf = 'IT lead', due = '2026-11-30' } = {}) {
  await userEvent.click(within(section()).getByRole('button', { name: 'Log a question' }))
  if (question) await userEvent.type(within(form()).getByLabelText('Question'), question)
  if (askedOf) await userEvent.type(within(form()).getByLabelText('For whom'), askedOf)
  if (due) await userEvent.type(within(form()).getByLabelText('Due date'), due)
}

describe.each(['audit-readiness', 'internal-improvement'] as const)('open questions under %s (ticket 414)', (goal) => {
  it.each([
    ['the question', { question: '' }, 'Say what is asked first.'],
    ['who it is for', { askedOf: '' }, 'Say who the question is for first.'],
    ['the due date', { due: '' }, 'Choose a due date first.'],
  ])('sends nothing without %s, naming it', async (_, blank, sentence) => {
    const sent = route()
    paper({ goal })
    await fill(blank)
    await userEvent.click(within(form()).getByRole('button', { name: 'Log question' }))
    expect(within(form()).getByRole('alert').textContent).toBe(sentence)
    expect(sent).toEqual([])
  })

  it('logs a question and lists it with who it is for and when it is due', async () => {
    const sent = route(200, { question: ASKED })
    const onQuestionsChanged = vi.fn()
    paper({ goal, onQuestionsChanged })
    expect(within(section()).getByText('No open questions on this control.')).toBeTruthy()
    await fill()
    await userEvent.click(within(form()).getByRole('button', { name: 'Log question' }))
    expect(sent).toEqual([{ url: '/api/controls/1.1.1/questions', method: 'POST',
      body: { question: 'Who reviews the ISMS scope each year?', asked_of: 'IT lead', due_date: '2026-11-30' } }])
    expect(within(section()).getByText('Who reviews the ISMS scope each year?')).toBeTruthy()
    expect(within(section()).getByText('For IT lead · Due 30 November 2026')).toBeTruthy()
    expect(within(section()).queryByRole('form')).toBeNull()
    expect(onQuestionsChanged).toHaveBeenCalledOnce()
  })

  it('records an answer and shows when and what', async () => {
    const sent = route(200, { question: ANSWERED })
    const onQuestionsChanged = vi.fn()
    paper({ goal, questions: [ASKED], onQuestionsChanged })
    await userEvent.click(within(section()).getByRole('button', { name: 'Record answer' }))
    const answering = within(section()).getByRole('form', { name: 'Record the answer' })
    await userEvent.click(within(answering).getByRole('button', { name: 'Save answer' }))
    expect(within(answering).getByRole('alert').textContent).toBe('Write the answer first.')
    expect(sent).toEqual([])
    await userEvent.type(within(answering).getByLabelText('Answer'), '  The CISO, each March. ')
    await userEvent.click(within(answering).getByRole('button', { name: 'Save answer' }))
    expect(sent).toEqual([{ url: '/api/controls/1.1.1/questions/7/answer', method: 'POST',
      body: { answer: 'The CISO, each March.' } }])
    expect(within(section()).getByText('Answered on 10 November 2026: The CISO, each March.')).toBeTruthy()
    expect(within(section()).queryByRole('button', { name: 'Record answer' })).toBeNull()
    expect(onQuestionsChanged).toHaveBeenCalledOnce()
  })

  it("shows a refusal with the server's sentence", async () => {
    const detail = 'The due date 2020-01-01 has passed. Choose today or a later date.'
    route(422, { detail })
    paper({ goal })
    await fill({ due: '2020-01-01' })
    await userEvent.click(within(form()).getByRole('button', { name: 'Log question' }))
    expect(within(form()).getByRole('alert').textContent).toBe(detail)
    expect(within(form()).getByRole('alert').className).toBe('field-note notice')
  })

  it("shows a refused answer with the server's sentence", async () => {
    const detail = 'This question was answered already: The CISO, each March.'
    route(409, { detail })
    paper({ goal, questions: [ASKED] })
    await userEvent.click(within(section()).getByRole('button', { name: 'Record answer' }))
    await userEvent.type(within(section()).getByLabelText('Answer'), 'Nobody.')
    await userEvent.click(within(section()).getByRole('button', { name: 'Save answer' }))
    expect(within(section()).getByRole('alert').textContent).toBe(detail)
  })

  it('says a log or an answer failed any other way', async () => {
    route(500)
    paper({ goal, questions: [ASKED] })
    await fill()
    await userEvent.click(within(form()).getByRole('button', { name: 'Log question' }))
    expect(within(form()).getByRole('alert').textContent)
      .toBe('The question could not be logged just now. Please try again.')
    await userEvent.click(within(section()).getByRole('button', { name: 'Record answer' }))
    await userEvent.type(within(section()).getByLabelText('Answer'), 'Yes.')
    await userEvent.click(within(section()).getByRole('button', { name: 'Save answer' }))
    expect(within(section()).getAllByRole('alert').map((a) => a.textContent))
      .toContain('The answer could not be saved just now. Please try again.')
  })

  it('is busy and disabled while a question is sent', async () => {
    let answer: (response: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { answer = resolve })))
    paper({ goal })
    await fill()
    await userEvent.click(within(form()).getByRole('button', { name: 'Log question' }))
    const submit = within(form()).getByRole('button', { name: 'Log question' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    expect(submit.getAttribute('aria-busy')).toBe('true')
    expect((within(form()).getByLabelText('Question') as HTMLTextAreaElement).disabled).toBe(true)
    answer(Response.json({ question: ASKED }))
    expect(await within(section()).findByText('For IT lead · Due 30 November 2026')).toBeTruthy()
  })

  it('Cancel closes the form, sending nothing', async () => {
    const sent = route()
    paper({ goal })
    await fill()
    await userEvent.click(within(form()).getByRole('button', { name: 'Cancel' }))
    expect(within(section()).queryByRole('form')).toBeNull()
    expect(sent).toEqual([])
  })

  it('tags an overdue question Overdue, in the stale flag style', () => {
    paper({ goal, questions: [{ ...ASKED, overdue: true }, { ...ANSWERED, id: 8 }] })
    const [overdue, answered] = within(section()).getAllByRole('listitem')
    expect(within(overdue).getByText('Overdue').className).toBe('flag')
    expect(within(answered).queryByText('Overdue')).toBeNull()
  })
})

describe('the open questions on an assessed control (ticket 414)', () => {
  it('sits after the review bar', () => {
    const accepted: Assessment = { control_id: '1.1.1', verdict: 'evidenced', answer: 'Policies are defined.',
      status: 'accepted', requirements: [], thread_id: 't1', review: null, review_step: null }
    render(<WorkingPaper overview={{ company: 'Nordlicht', protection_level: 'normal', answer_language: 'en',
      requirements: {}, controls: [CARD], goal: 'audit-readiness' }} card={{ ...CARD, status: 'accepted' }}
      running={null} run={{ status: 'done', steps: [], assessment: accepted }} onAssess={() => {}} onOpen={() => {}}
      onOverview={() => {}} onReviewed={() => {}} onRefresh={() => Promise.resolve(true)} onItemDecided={() => {}} />)
    const review = screen.getByRole('region', { name: 'Review' })
    expect(review.compareDocumentPosition(section()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
