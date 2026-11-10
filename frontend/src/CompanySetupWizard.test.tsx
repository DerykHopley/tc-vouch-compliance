import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanySetupWizard from './CompanySetupWizard.tsx'
import { BACKUP_POLICY } from './libraryFixtures.ts'
import type { ProfileSummary } from './profiles.ts'
import { setOnSignedOut } from './session.ts'
import type { LibraryDocument, ProtectionScope } from './setup.ts'

const LEVELS: ProtectionScope[] = [
  { level: 'normal', requirements: { must: 37, should: 48 }, total: 85, controls: 12, added: 0, added_controls: [] },
  { level: 'high', requirements: { must: 37, should: 48, high: 14 }, total: 99, controls: 12, added: 14,
    added_controls: ['1.6.2', '4.1.2'] },
  { level: 'very high', requirements: { must: 37, should: 48, high: 14, 'very high': 10 }, total: 109, controls: 12,
    added: 10, added_controls: ['1.6.1'] },
]

/** The base framework's twelve control ids, as GET /api/developer/controls sends them (questions stand in). */
const CONTROLS = ['1.1.1', '1.6.1', '1.6.2', '2.1.3', '4.1.2', '4.1.3', '4.2.1', '5.1.1', '5.1.2', '5.2.4', '5.2.5', '5.2.9']
  .map((id) => ({ id, question: `To what extent is ${id} handled?` }))

const libraryDocument = (id: string, title: string, more: Partial<LibraryDocument> = {}): LibraryDocument => ({
  id, source: 'policies', title, kind: 'documented', version: '1.0', approval_date: '2026-07-20', review_cycle: 'yearly',
  stale_because: null, stale: false, language: 'en', ...more,
})
/** The sample library as GET /api/developer/sample-library sends it: two policies (one stale), then a record. */
const LIBRARY: LibraryDocument[] = [
  libraryDocument('access-rights-policy', 'Access rights policy'),
  BACKUP_POLICY,
  libraryDocument('training-record', 'Security training record 2026', { source: 'records', kind: 'implemented' }),
]

const levelsAnswer = () => new Response(JSON.stringify({ levels: LEVELS }))
const controlsAnswer = () => new Response(JSON.stringify({ controls: CONTROLS }))
const libraryAnswer = () => new Response(JSON.stringify({ documents: LIBRARY }))
const CONTROLS_PATH = '/api/developer/controls'
const LIBRARY_PATH = '/api/developer/sample-library'
const PROFILES_PATH = '/api/developer/profiles'
const LIST_PATH = '/api/profiles'
const CREATED: ProfileSummary = { id: 'musterfirma-gmbh', company: 'Musterfirma GmbH', country: 'Germany',
  answer_language: 'de' }
const createdAnswer = () => Response.json(CREATED, { status: 201 })
/** GET /api/profiles's answer, listing `profiles`. */
const listing = (...profiles: ProfileSummary[]) => Response.json({ profiles, default: null })

type Answer = Response | 'offline'

/** fetch answering the scope read with `levels` in turn, the controls read with `controls` in turn, the library
 * read with `library` in turn, the create with `created` in turn and the profile list (read before a create, and
 * again after a lost answer) with `listed` in turn (a Response, or 'offline' to reject), then with the full answer (a
 * create: 201; the list: no profiles). */
function api(levels: Answer[] = [], controls: Answer[] = [], library: Answer[] = [], created: Answer[] = [],
  listed: Answer[] = []) {
  const fetch = vi.fn((url: string, _init?: RequestInit) => {
    const next = url === CONTROLS_PATH ? controls.shift() ?? controlsAnswer()
      : url === LIBRARY_PATH ? library.shift() ?? libraryAnswer()
        : url === PROFILES_PATH ? created.shift() ?? createdAnswer()
          : url === LIST_PATH ? listed.shift() ?? listing()
            : levels.shift() ?? levelsAnswer()
    return next === 'offline' ? Promise.reject(new TypeError('offline')) : Promise.resolve(next)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

/** The wizard's clock (performance.now): it ignores a move within 400 ms of the last, so each helper press below comes
 * a second after the one before, as a person's separate presses do. */
let clock = 0
beforeEach(() => {
  clock = 0
  vi.spyOn(performance, 'now').mockImplementation(() => clock)
})

afterEach(() => {
  cleanup()
  setOnSignedOut(null)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const nextButton = () => screen.getByRole('button', { name: 'Next' })
const backButton = () => screen.getByRole('button', { name: 'Back' })
const next = () => { clock += 1000; return userEvent.click(nextButton()) }
const back = () => { clock += 1000; return userEvent.click(backButton()) }
const heading = (name: string) => screen.getByRole('heading', { name })
const nameField = () => screen.getByRole('textbox', { name: 'Company name' })
const countryField = () => screen.getByRole('combobox', { name: 'Country' })

async function stepOne(company = 'Musterfirma GmbH', country = 'Germany') {
  if (company) await userEvent.type(nameField(), company)
  if (country) await userEvent.selectOptions(countryField(), country)
}

async function toStep3() {
  await stepOne()
  await next()
  await next()
}

async function toStep5() {
  await toStep3()
  await userEvent.click(await screen.findByRole('radio', { name: /^Normal/ }))
  await next()
  await next() // step 4: the Goal, Internal improvement kept
}

/** A control's checkbox on step 5, by its id (its label starts with the id). */
const controlName = (id: string) => new RegExp(`^${id.replace(/\./g, '\\.')} `)
const control = (id: string) => screen.getByRole('checkbox', { name: controlName(id) })
const findControl = (id: string) => screen.findByRole('checkbox', { name: controlName(id) })
const reason = (id: string) => screen.getByRole('textbox', { name: `Why ${id} doesn't apply` })

async function toStep6() {
  await toStep5()
  await findControl('1.1.1')
  await next()
}

const choose = (title: string) => screen.getByRole('checkbox', { name: `Choose ${title}` })
const findChoose = (title: string) => screen.findByRole('checkbox', { name: `Choose ${title}` })

/** Steps 1 to 6 answered: Musterfirma GmbH (typed with spaces around it) in Germany, answers in German, protection level High, 5.1.1 then 1.6.1
 * marked not applicable (1.6.1's reason typed with spaces around it), the backup policy and the training record
 * chosen; then Next to step 7. */
async function toStep7() {
  await stepOne('  Musterfirma GmbH ')
  await next()
  await userEvent.click(screen.getByRole('radio', { name: 'German' }))
  await next()
  await userEvent.click(await screen.findByRole('radio', { name: /^High/ }))
  await next()
  await next() // step 4: the Goal, Internal improvement kept
  await userEvent.click(await findControl('5.1.1'))
  await userEvent.type(reason('5.1.1'), 'No cryptography')
  await userEvent.click(control('1.6.1'))
  await userEvent.type(reason('1.6.1'), '  Remote only  ')
  await next()
  await userEvent.click(await findChoose('Backup policy'))
  await userEvent.click(choose('Security training record 2026'))
  await next()
}

const createButton = () => screen.getByRole('button', { name: 'Create company' })
const create = () => { clock += 1000; return userEvent.click(createButton()) }
const posts = (fetch: ReturnType<typeof api>) => fetch.mock.calls.filter((c) => c[0] === PROFILES_PATH)
/** The wizard on its own, `onCreated` spied. */
function wizard(onDeveloper: () => void = () => {}) {
  const onCreated = vi.fn()
  render(<CompanySetupWizard developerPath="/developer" onDeveloper={onDeveloper} onCreated={onCreated} />)
  return onCreated
}

describe('company setup', () => {
  it('lists all seven steps, marks the current one, and starts on step 1 with no Back', () => {
    api()
    wizard()
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps.map((s) => s.textContent)).toEqual([
      '1Company', '2Language', '3Protection level', '4Goal', '5Not applicable',
      '6Evidence', '7Finish'])
    expect(steps[0].getAttribute('aria-current')).toBe('step')
    expect(screen.getByText('Step 1 of 7')).toBeTruthy()
    expect(heading('Company')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
    expect(screen.queryAllByRole('link').map((l) => l.textContent)).toEqual(['Developer'])
    expect(nameField().getAttribute('maxLength')).toBe('100')
    expect((countryField() as HTMLSelectElement).value).toBe('')
  })

  it.each([
    ['a blank name', '   ', 'Germany', ['Give the company a name.'], 'Company name'],
    ['no country', 'Musterfirma', '', ["Choose the company's country."], 'Country'],
    ['neither', '', '', ['Give the company a name.', "Choose the company's country."], 'Company name'],
  ])('Next with %s stays on step 1, marks each missing field and focuses the first', async (_, company, country, messages, first) => {
    api()
    wizard()
    await stepOne(company, country)
    await next()
    expect(heading('Company')).toBeTruthy()
    const invalid = [nameField(), countryField()].filter((f) => f.getAttribute('aria-invalid') === 'true')
    expect(invalid.map((f) => f.getAttribute('aria-describedby') && document.getElementById(f.getAttribute('aria-describedby')!)?.textContent))
      .toEqual(messages)
    expect(document.activeElement).toBe(first === 'Country' ? countryField() : nameField())
  })

  it('clears a marked field as soon as it is fixed, before Next', async () => {
    api()
    wizard()
    await next()
    expect(nameField().getAttribute('aria-invalid')).toBe('true')
    expect(countryField().getAttribute('aria-invalid')).toBe('true')
    await userEvent.type(nameField(), 'M')
    expect(nameField().getAttribute('aria-invalid')).toBeNull()
    expect(screen.queryByText('Give the company a name.')).toBeNull()
    expect(countryField().getAttribute('aria-invalid')).toBe('true') // the other field keeps its problem
    await userEvent.selectOptions(countryField(), 'Germany')
    expect(screen.queryByText("Choose the company's country.")).toBeNull()
  })

  it('moves on with Enter, as Next does', async () => {
    api()
    wizard()
    await stepOne('Musterfirma GmbH')
    await userEvent.type(nameField(), '{Enter}')
    expect(heading('Language')).toBeTruthy()
  })

  it('moves focus to each new step heading, and marks it current in the step list', async () => {
    api()
    wizard()
    await stepOne()
    await next()
    expect(document.activeElement).toBe(heading('Language'))
    const steps = () => within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps().map((s) => s.getAttribute('aria-current'))).toEqual([null, 'step', null, null, null, null, null])
    expect(screen.getByText('Step 2 of 7')).toBeTruthy()
    await next()
    expect(document.activeElement).toBe(heading('Protection level'))
    expect(steps()[2].getAttribute('aria-current')).toBe('step')
    await back()
    expect(document.activeElement).toBe(heading('Language'))
  })

  it('keeps every answer going back and forward', async () => {
    api()
    wizard()
    await stepOne('Musterfirma GmbH', 'Austria')
    await next()
    const english = screen.getByRole('radio', { name: 'English' }) as HTMLInputElement
    expect(english.checked).toBe(true)
    expect(screen.getByRole('group', { name: 'Answer language' }).textContent)
      .toContain("Vouch's answers, the chat and the readiness report use this language; the interface stays English.")
    await userEvent.click(screen.getByRole('radio', { name: 'German' }))
    await next()
    await userEvent.click(await screen.findByRole('radio', { name: /^High/ }))
    await back()
    await back()
    expect((nameField() as HTMLInputElement).value).toBe('Musterfirma GmbH')
    expect((countryField() as HTMLSelectElement).value).toBe('Austria')
    await next()
    expect((screen.getByRole('radio', { name: 'German' }) as HTMLInputElement).checked).toBe(true)
    await next()
    expect((screen.getByRole('radio', { name: /^High/ }) as HTMLInputElement).checked).toBe(true)
  })

  it("step 3 shows each level's scope from the route, read once", async () => {
    const fetch = api()
    wizard()
    await stepOne()
    await next()
    await next()
    const levels = screen.getByRole('group', { name: 'Protection level' })
    expect(within(levels).getAllByRole('radio').map((r) => (r as HTMLInputElement).checked)).toEqual([false, false, false])
    await waitFor(() => expect(levels.textContent).toContain('Must and should requirements: 85 in 12 controls.'))
    expect(levels.textContent).toContain('Adds 14 high-protection requirements, in 2 controls (1.6.2, 4.1.2): 99 in all.')
    expect(levels.textContent).toContain('Adds 10 very-high-protection requirements, in 1 control (1.6.1): 109 in all.')
    expect(screen.queryByText(/not built yet/)).toBeNull()
    await back()
    await next() // the scope is read once, when step 3 first opens
    expect(fetch.mock.calls.map((c) => (c as unknown[])[0])).toEqual(['/api/developer/protection-levels'])
  })

  it('says while the scope loads, and when it fails offers Try again, the options still choosable', async () => {
    let answer: (r: Response) => void = () => {}
    const fetch = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { answer = resolve }))
      .mockImplementationOnce(() => Promise.reject(new TypeError('offline')))
      .mockImplementationOnce(() => Promise.resolve(levelsAnswer()))
    vi.stubGlobal('fetch', fetch)
    wizard()
    await stepOne()
    await next()
    await next()
    expect(screen.getAllByText('Loading what this level brings in…')).toHaveLength(3)
    answer(new Response('{}', { status: 500 }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('What each level brings into scope could not be loaded.')
    await userEvent.click(screen.getByRole('radio', { name: /^Very high/ }))
    expect((screen.getByRole('radio', { name: /^Very high/ }) as HTMLInputElement).checked).toBe(true)
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('alert')).toBeTruthy() // offline: fails again
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(screen.getByRole('group', { name: 'Protection level' }).textContent).toContain('85 in 12 controls')
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(fetch.mock.calls.every((c) => c[0] === '/api/developer/protection-levels')).toBe(true)
  })

  it('Next on step 3 with no level stays, marks the levels and focuses the first; choosing one clears it', async () => {
    api()
    wizard()
    await toStep3()
    await next()
    const levels = screen.getByRole('group', { name: 'Protection level' })
    expect(heading('Protection level')).toBeTruthy()
    expect(levels.getAttribute('aria-invalid')).toBe('true')
    expect(levels.getAttribute('aria-describedby')
      && document.getElementById(levels.getAttribute('aria-describedby')!)?.textContent).toBe('Choose a protection level.')
    expect(document.activeElement).toBe(within(levels).getAllByRole('radio')[0])
    await userEvent.click(screen.getByRole('radio', { name: /^High/ }))
    expect(levels.getAttribute('aria-invalid')).toBeNull()
    expect(screen.queryByText('Choose a protection level.')).toBeNull()
  })

  /** Steps 1 to 3 answered, a Normal level chosen, then Next onto step 4. */
  async function toStep4() {
    await toStep3()
    await userEvent.click(await screen.findByRole('radio', { name: /^Normal/ }))
    await next()
  }
  const goalRadio = (name: RegExp) => screen.getByRole<HTMLInputElement>('radio', { name })

  it('Next on step 3 with a level opens step 4, the Goal, Internal improvement chosen', async () => {
    api()
    wizard()
    await toStep4()
    expect(screen.getByText('Step 4 of 7')).toBeTruthy()
    expect(document.activeElement).toBe(heading('Goal'))
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps.map((s) => s.getAttribute('aria-current'))).toEqual([null, null, null, 'step', null, null, null])
    expect(screen.queryByText(/not yet available/)).toBeNull()
  })

  it('step 4 offers both Goals with their lines, Internal improvement first and chosen, and says it can change later',
    async () => {
    api()
    wizard()
    await toStep4()
    const goals = screen.getByRole('group', { name: 'Goal' })
    expect(within(goals).getAllByRole('radio').map((r) => r.closest('label')?.textContent)).toEqual([
      "Internal improvementImproving the company's own security, with no audit planned.",
      'Audit readinessPreparing for an external audit.',
    ])
    expect(goalRadio(/^Internal improvement/).checked).toBe(true)
    expect(goals.textContent).toContain('You can change the Goal later on the overview.')
  })

  it('Next on step 4 opens step 5, a Goal always being chosen; Back returns to it with the answer kept, then to step 3',
    async () => {
    api()
    wizard()
    await toStep4()
    await next()
    expect(screen.getByText('Step 5 of 7')).toBeTruthy()
    expect(document.activeElement).toBe(heading('Not applicable'))
    await back()
    expect(screen.getByText('Step 4 of 7')).toBeTruthy()
    expect(goalRadio(/^Internal improvement/).checked).toBe(true)
    await back()
    expect(heading('Protection level')).toBeTruthy()
  })

  it('keeps Audit readiness through step 7 and back; step 7 shows it and says a profile pass runs', async () => {
    api()
    wizard()
    await toStep4()
    await userEvent.click(goalRadio(/^Audit readiness/))
    await next()
    await findControl('1.1.1')
    await next()
    await userEvent.click(await findChoose('Backup policy'))
    await next()
    const summary = heading('Finish').parentElement?.querySelector('dl') as HTMLElement
    const terms = within(summary).getAllByRole('term').map((t) => t.textContent)
    expect(terms.indexOf('Goal')).toBe(terms.indexOf('Protection level') + 1)
    expect(within(summary).getAllByRole('definition')[terms.indexOf('Goal')].textContent).toBe('Audit readiness')
    expect(screen.getByText(/Then Vouch sets its Goal and runs a profile pass/)).toBeTruthy()
    await back()
    await back()
    await back()
    expect(screen.getByText('Step 4 of 7')).toBeTruthy()
    expect(goalRadio(/^Audit readiness/).checked).toBe(true)
    expect(goalRadio(/^Internal improvement/).checked).toBe(false)
  })

  it('step 5 lists every control unticked, under the sentence that says what not applicable means', async () => {
    api()
    wizard()
    await toStep5()
    expect(screen.getByText('Mark a control not applicable only when it doesn\'t apply to the company at all; the '
      + 'assessment leaves it out and the readiness report lists it with your reason.')).toBeTruthy()
    const boxes = await screen.findAllByRole('checkbox')
    expect(boxes.map((b) => b.closest('label')?.textContent)).toEqual(CONTROLS.map((c) => `${c.id} ${c.question}`))
    expect(boxes.every((b) => !(b as HTMLInputElement).checked)).toBe(true)
    expect(screen.queryAllByRole('textbox')).toEqual([])
    expect(screen.queryByText(/not built yet/)).toBeNull()
  })

  it('ticking a control asks why; unticking drops the reason, and ticking again starts empty', async () => {
    api()
    wizard()
    await toStep5()
    await userEvent.click(await findControl('5.1.1'))
    await userEvent.type(reason('5.1.1'), 'No cryptography in use')
    await userEvent.click(control('5.1.1'))
    expect(screen.queryByRole('textbox', { name: "Why 5.1.1 doesn't apply" })).toBeNull()
    await userEvent.click(control('5.1.1'))
    expect((reason('5.1.1') as HTMLTextAreaElement).value).toBe('')
  })

  it('Next with a blank reason stays on step 5, marks each blank one and focuses the first; typing clears it', async () => {
    api()
    wizard()
    await toStep5()
    await userEvent.click(await findControl('1.6.1'))
    await userEvent.click(control('4.1.2'))
    await userEvent.click(control('5.2.9'))
    await userEvent.type(reason('1.6.1'), '   ')
    await userEvent.type(reason('4.1.2'), 'No prototypes')
    await next()
    expect(heading('Not applicable')).toBeTruthy()
    const problem = (id: string) => {
      const field = reason(id)
      const note = field.getAttribute('aria-describedby')
      return [field.getAttribute('aria-invalid'), note && document.getElementById(note)?.textContent]
    }
    expect(problem('1.6.1')).toEqual(['true', "Say why 1.6.1 doesn't apply."])
    expect(problem('4.1.2')).toEqual([null, null])
    expect(problem('5.2.9')).toEqual(['true', "Say why 5.2.9 doesn't apply."])
    expect(document.activeElement).toBe(reason('1.6.1'))
    await userEvent.type(reason('1.6.1'), 'x')
    expect(problem('1.6.1')).toEqual([null, null])
    expect(problem('5.2.9')[0]).toBe('true')
  })

  it('Next with every control ticked stays on step 5 and says one must apply', async () => {
    api()
    wizard()
    await toStep5()
    await findControl(CONTROLS[0].id)
    for (const c of CONTROLS) {
      await userEvent.click(control(c.id))
      await userEvent.type(reason(c.id), 'Not here')
    }
    await next()
    expect(heading('Not applicable')).toBeTruthy()
    const list = screen.getByRole('group', { name: 'Controls not applicable' })
    expect(list.getAttribute('aria-invalid')).toBe('true')
    expect(list.getAttribute('aria-describedby')
      && document.getElementById(list.getAttribute('aria-describedby')!)?.textContent).toBe('At least one control must apply.')
    expect(document.activeElement).toBe(control(CONTROLS[0].id))
    await userEvent.click(control(CONTROLS[3].id))
    expect(list.getAttribute('aria-invalid')).toBeNull()
  })

  it('keeps the ticked controls and their reasons as typed through Back and Next', async () => {
    api()
    wizard()
    await toStep5()
    await userEvent.click(await findControl('1.6.1'))
    await userEvent.type(reason('1.6.1'), 'No prototype vehicles ')
    await back()
    await next()
    expect(((await findControl('1.6.1')) as HTMLInputElement).checked).toBe(true)
    expect((reason('1.6.1') as HTMLTextAreaElement).value).toBe('No prototype vehicles ')
    expect((control('4.1.2') as HTMLInputElement).checked).toBe(false)
  })

  it('says while the controls load, and when they fail offers Try again, which reads once more', async () => {
    let answer: (r: Response) => void = () => {}
    const pending = new Promise<Response>((resolve) => { answer = resolve })
    const fetch = api()
    fetch.mockImplementation((url: string) => {
      if (url !== '/api/developer/controls') return Promise.resolve(levelsAnswer())
      const reads = fetch.mock.calls.filter((c) => c[0] === url).length
      return reads === 1 ? pending : Promise.resolve(controlsAnswer())
    })
    wizard()
    await toStep5()
    expect(screen.getByText('Loading the controls…')).toBeTruthy()
    answer(new Response('{}', { status: 503 }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('The controls could not be loaded.')
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findAllByRole('checkbox')).toHaveLength(12)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(fetch.mock.calls.filter((c) => c[0] === '/api/developer/controls')).toHaveLength(2)
  })

  it('a control read that fails while offline shows the alert too', async () => {
    api([], ['offline'])
    wizard()
    await toStep5()
    expect((await screen.findByRole('alert')).textContent).toContain('The controls could not be loaded.')
  })

  it('reads the controls once, and posts no profile at any step', async () => {
    const fetch = api()
    wizard()
    await toStep5()
    await userEvent.click(await findControl('1.6.1'))
    await userEvent.type(reason('1.6.1'), 'No prototype vehicles')
    await next()
    await back()
    await next()
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(['/api/developer/protection-levels', CONTROLS_PATH, LIBRARY_PATH])
  })

  it('a second press within 400 ms of a move is ignored, on Next and on Back, so each press moves one step', async () => {
    api()
    wizard()
    await stepOne()
    await next()
    await userEvent.click(nextButton()) // the same moment: a double-click's second press
    expect(screen.getByText('Step 2 of 7')).toBeTruthy()
    clock += 399
    await userEvent.click(nextButton())
    expect(screen.getByText('Step 2 of 7')).toBeTruthy()
    clock += 1
    await userEvent.click(nextButton())
    await userEvent.click(nextButton()) // lands on step 3's Next: no move, and no "Choose a protection level."
    expect(screen.getByText('Step 3 of 7')).toBeTruthy()
    expect(screen.queryByText('Choose a protection level.')).toBeNull()
    clock += 1000
    await userEvent.click(backButton())
    await userEvent.click(backButton())
    expect(screen.getByText('Step 2 of 7')).toBeTruthy()
  })

  it("leaving a step clears its problems: they don't show again on return before Next", async () => {
    api()
    wizard()
    await toStep3()
    await next()
    expect(screen.getByText('Choose a protection level.')).toBeTruthy()
    await back()
    await next()
    expect(screen.queryByText('Choose a protection level.')).toBeNull()
    expect(screen.getByRole('group', { name: 'Protection level' }).getAttribute('aria-invalid')).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: /^Normal/ }))
    await next()
    await next()
    await userEvent.click(await findControl('1.6.1'))
    await next()
    expect(reason('1.6.1').getAttribute('aria-invalid')).toBe('true')
    await back()
    await next()
    expect(reason('1.6.1').getAttribute('aria-invalid')).toBeNull()
    expect(screen.queryByText("Say why 1.6.1 doesn't apply.")).toBeNull()
  })

  it('Next on step 5 with good answers goes to step 6, focus on its heading, and keeps the reasons as typed', async () => {
    api()
    wizard()
    await toStep5()
    await userEvent.click(await findControl('1.6.1'))
    await userEvent.type(reason('1.6.1'), ' No prototype vehicles ')
    await next()
    expect(screen.getByText('Step 6 of 7')).toBeTruthy()
    expect(document.activeElement).toBe(heading('Evidence'))
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps[5].textContent).toBe('6Evidence')
    expect(steps.map((s) => s.getAttribute('aria-current'))).toEqual([null, null, null, null, null, 'step', null])
    await back()
    expect((reason('1.6.1') as HTMLTextAreaElement).value).toBe(' No prototype vehicles ')
  })

  it('step 6 lists the library under Policies and Records, none chosen, under the sentence that says what it is for', async () => {
    api()
    wizard()
    await toStep6()
    expect(screen.getByText("Choose the documents that are this company's evidence. Vouch searches and cites only these."))
      .toBeTruthy()
    const policies = await screen.findByRole('table', { name: 'Policies' })
    expect(within(policies).getAllByRole('checkbox').map((b) => b.getAttribute('aria-label')))
      .toEqual(['Choose Access rights policy', 'Choose Backup policy'])
    const records = screen.getByRole('table', { name: 'Records' })
    expect(within(records).getAllByRole('checkbox').map((b) => b.getAttribute('aria-label')))
      .toEqual(['Choose Security training record 2026'])
    expect(screen.getAllByRole('checkbox').every((b) => !(b as HTMLInputElement).checked)).toBe(true)
    expect(screen.getByRole('status').textContent).toBe('No documents chosen yet.')
  })

  it('the ready-to-cite line follows each tick', async () => {
    api()
    wizard()
    await toStep6()
    await userEvent.click(await findChoose('Backup policy'))
    expect(screen.getByRole('status').textContent).toBe('Ready to cite: 1 document (1 documented, 0 implemented, 1 stale).')
    await userEvent.click(choose('Access rights policy'))
    await userEvent.click(choose('Security training record 2026'))
    expect(screen.getByRole('status').textContent).toBe('Ready to cite: 3 documents (2 documented, 1 implemented, 1 stale).')
    await userEvent.click(choose('Backup policy'))
    expect(screen.getByRole('status').textContent).toBe('Ready to cite: 2 documents (1 documented, 1 implemented).')
  })

  it('Next on step 6 with no document stays, says so and focuses the first checkbox; ticking one clears it', async () => {
    api()
    wizard()
    await toStep6()
    await findChoose('Access rights policy')
    await next()
    expect(heading('Evidence')).toBeTruthy()
    const group = screen.getByRole('group', { name: 'Evidence documents' })
    expect(group.getAttribute('aria-invalid')).toBe('true')
    expect(group.getAttribute('aria-describedby')
      && document.getElementById(group.getAttribute('aria-describedby')!)?.textContent).toBe('Choose at least one document.')
    expect(document.activeElement).toBe(choose('Access rights policy'))
    await userEvent.click(choose('Security training record 2026'))
    expect(screen.queryByText('Choose at least one document.')).toBeNull()
    expect(group.getAttribute('aria-invalid')).toBeNull()
  })

  it('Next on step 6 with no policies in the library focuses the first record', async () => {
    api([], [], [new Response(JSON.stringify({ documents: LIBRARY.filter((d) => d.source === 'records') }))])
    wizard()
    await toStep6()
    await findChoose('Security training record 2026')
    await next()
    expect(document.activeElement).toBe(choose('Security training record 2026'))
  })

  it('Next on step 6 while the library loads focuses the step heading, and focus stays there when it lands', async () => {
    let land: (r: Response) => void = () => {}
    const fetch = api()
    fetch.mockImplementation((url: string) => url === LIBRARY_PATH ? new Promise<Response>((resolve) => { land = resolve })
      : Promise.resolve(url === CONTROLS_PATH ? controlsAnswer() : levelsAnswer()))
    wizard()
    await toStep6()
    await next()
    expect(screen.getByText('Choose at least one document.')).toBeTruthy()
    expect(document.activeElement).toBe(heading('Evidence'))
    land(libraryAnswer())
    await findChoose('Backup policy')
    expect(document.activeElement).toBe(heading('Evidence'))
  })

  it.each([
    ['3', toStep3, 'Choose a protection level.'],
    ['6', async () => { await toStep6(); await findChoose('Backup policy') }, 'Choose at least one document.'],
  ])("step %s's problem shows below Back and Next, so a refused Next moves neither", async (_, to, words) => {
    api()
    wizard()
    await to()
    await next()
    const notice = screen.getByText(words)
    expect(nextButton().compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(backButton().compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  /** Step 5 with 1.6.1 marked, its reason field showing; `settled`: a second after the move, else the same moment. */
  async function step5Marked(settled: boolean) {
    api()
    wizard()
    await toStep5()
    await userEvent.click(await findControl('1.6.1'))
    if (settled) clock += 1000
  }
  const targets: Record<string, () => Element> = {
    'a reason field': () => reason('1.6.1'),
    'a checkbox': () => control('4.1.2'),
    'the page': () => document.body,
    'Next': nextButton,
  }
  it.each(Object.keys(targets).flatMap((target) => [1, 2].flatMap((detail) => [true, false].map((settled) => ({
    target, detail, settled, kept: detail > 1 && target !== 'a reason field' })))))(
    "a press of detail $detail on $target (settled: $settled) is kept from acting: $kept", async ({ target, detail, settled, kept }) => {
      await step5Marked(settled)
      const on = targets[target]()
      expect(fireEvent.mouseDown(on, { detail })).toBe(!kept)
      if (target !== 'Next') expect(fireEvent.click(on, { detail })).toBe(!kept) // Next's click would submit
    })

  it("a double-click's second press doesn't tick a box: its first press does, once", async () => {
    await step5Marked(true)
    await userEvent.dblClick(control('4.1.2'))
    expect((control('4.1.2') as HTMLInputElement).checked).toBe(true)
  })

  it('Next on step 6 with a document chosen goes to step 7, focus on its heading; Back returns with the choices kept',
    async () => {
      api()
      wizard()
      await toStep6()
      await userEvent.click(await findChoose('Backup policy'))
      await next()
      expect(screen.getByText('Step 7 of 7')).toBeTruthy()
      expect(document.activeElement).toBe(heading('Finish'))
      const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
      expect(steps.map((s) => s.getAttribute('aria-current'))).toEqual([null, null, null, null, null, null, 'step'])
      expect(screen.queryByRole('button', { name: 'Next' })).toBeNull()
      expect(screen.queryByText(/not built yet/)).toBeNull()
      await back()
      expect(document.activeElement).toBe(heading('Evidence'))
      expect((choose('Backup policy') as HTMLInputElement).checked).toBe(true)
      expect((choose('Access rights policy') as HTMLInputElement).checked).toBe(false)
    })

  /** Step 6's Select all or Deselect all (#475) for a folder, by the name a screen reader announces. */
  const folderButton = (name: string) => screen.getByRole('button', { name })
  const ticked = () => screen.getAllByRole('checkbox').filter((b) => (b as HTMLInputElement).checked)
    .map((b) => b.getAttribute('aria-label'))

  it('Select all and Deselect all choose or clear one folder, leaving the other as it is', async () => {
    api()
    wizard()
    await toStep6()
    await findChoose('Backup policy')
    await userEvent.click(folderButton('Select all policies'))
    expect(ticked()).toEqual(['Choose Access rights policy', 'Choose Backup policy'])
    expect(screen.getByRole('status').textContent).toBe('Ready to cite: 2 documents (2 documented, 0 implemented, 1 stale).')
    await userEvent.click(folderButton('Select all records'))
    expect(ticked()).toEqual(['Choose Access rights policy', 'Choose Backup policy', 'Choose Security training record 2026'])
    await userEvent.click(folderButton('Deselect all policies'))
    expect(ticked()).toEqual(['Choose Security training record 2026'])
    await userEvent.click(folderButton('Select all policies'))
    await userEvent.click(folderButton('Deselect all records'))
    expect(ticked()).toEqual(['Choose Access rights policy', 'Choose Backup policy'])
    await userEvent.click(folderButton('Deselect all policies'))
    expect(ticked()).toEqual([])
    expect(screen.getByRole('status').textContent).toBe('No documents chosen yet.')
  })

  it('Select all after a tick chooses each document once, in the library\'s order, and sends them', async () => {
    const fetch = api()
    wizard()
    await toStep6()
    await userEvent.click(await findChoose('Backup policy'))
    await userEvent.click(folderButton('Select all policies'))
    await userEvent.click(folderButton('Select all policies'))
    await next()
    await create()
    const body = JSON.parse(String(posts(fetch)[0][1]?.body)) as { evidence: unknown }
    expect(body.evidence).toEqual({ policies: ['access-rights-policy', 'backup-policy'], records: [] })
  })

  it('Select all clears "Choose at least one document."; after Deselect all, Next says it again', async () => {
    api()
    wizard()
    await toStep6()
    await findChoose('Backup policy')
    await next()
    expect(screen.getByText('Choose at least one document.')).toBeTruthy()
    await userEvent.click(folderButton('Select all records'))
    expect(screen.queryByText('Choose at least one document.')).toBeNull()
    await userEvent.click(folderButton('Deselect all records'))
    await next()
    expect(heading('Evidence')).toBeTruthy()
    expect(screen.getByText('Choose at least one document.')).toBeTruthy()
  })

  it('a folder with no documents in the library has no Select all or Deselect all', async () => {
    api([], [], [new Response(JSON.stringify({ documents: LIBRARY.filter((d) => d.source === 'records') }))])
    wizard()
    await toStep6()
    await findChoose('Security training record 2026')
    expect(screen.queryByRole('button', { name: /all policies$/ })).toBeNull()
    expect(folderButton('Select all records')).toBeTruthy()
  })

  it('keeps the chosen documents through Back and Next', async () => {
    api()
    wizard()
    await toStep6()
    await userEvent.click(await findChoose('Backup policy'))
    await userEvent.click(choose('Security training record 2026'))
    await back()
    await next()
    expect((choose('Backup policy') as HTMLInputElement).checked).toBe(true)
    expect((choose('Security training record 2026') as HTMLInputElement).checked).toBe(true)
    expect((choose('Access rights policy') as HTMLInputElement).checked).toBe(false)
  })

  it.each([
    ['an error answer', new Response('{}', { status: 500 }) as Answer],
    ['offline', 'offline' as Answer],
  ])('when the library read fails (%s) it says so with Try again, which reads once more and lists it', async (_, failure) => {
    const fetch = api([], [], [failure])
    wizard()
    await toStep6()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('The sample library could not be loaded.')
    expect(screen.queryAllByRole('checkbox')).toEqual([])
    await next() // nothing loaded: stays and says so, focus on the heading, which outlasts Try again
    expect(screen.getByText('Choose at least one document.')).toBeTruthy()
    expect(document.activeElement).toBe(heading('Evidence'))
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await findChoose('Backup policy')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(fetch.mock.calls.filter((c) => c[0] === LIBRARY_PATH)).toHaveLength(2)
  })

  it('says while the library loads', async () => {
    const fetch = api()
    fetch.mockImplementation((url: string) => url === LIBRARY_PATH ? new Promise<Response>(() => {})
      : Promise.resolve(url === CONTROLS_PATH ? controlsAnswer() : levelsAnswer()))
    wizard()
    await toStep6()
    expect(screen.getByText('Loading the sample library…')).toBeTruthy()
  })

  it('reads the library once across Back and Next, and posts no profile at any step', async () => {
    const fetch = api()
    wizard()
    await toStep6()
    await userEvent.click(await findChoose('Backup policy'))
    await next()
    await back()
    await next()
    await back()
    await back()
    await next()
    await next()
    expect(fetch.mock.calls.map((c) => c[0]))
      .toEqual(['/api/developer/protection-levels', CONTROLS_PATH, LIBRARY_PATH])
  })

  it('the breadcrumb leads back to the developer page', async () => {
    api()
    const onDeveloper = vi.fn()
    wizard(onDeveloper)
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(crumbs.textContent).toBe('Developer›Company setup')
    expect(within(crumbs).getByRole('link', { name: 'Developer' }).getAttribute('href')).toBe('/developer')
    await userEvent.click(within(crumbs).getByRole('link', { name: 'Developer' }))
    expect(onDeveloper).toHaveBeenCalled()
  })

  it('step 7 sums up every answer, the reasons trimmed in the framework\'s order, the documents with no checkboxes',
    async () => {
      api()
      wizard()
      await toStep7()
      const summary = heading('Finish').parentElement?.querySelector('dl')
      expect(summary).toBeTruthy()
      const terms = within(summary as HTMLElement).getAllByRole('term').map((t) => t.textContent)
      const values = within(summary as HTMLElement).getAllByRole('definition').map((d) => d.textContent)
      expect(terms).toEqual(['Company', 'Country', 'Answer language', 'Protection level', 'Goal', 'Not applicable',
        'Ready to cite'])
      expect(values.slice(0, 5)).toEqual(['Musterfirma GmbH', 'Germany', 'German', 'High', 'Internal improvement'])
      expect(within(summary as HTMLElement).getAllByRole('listitem').map((i) => i.textContent))
        .toEqual(['1.6.1: Remote only', '5.1.1: No cryptography'])
      expect(values[6]).toContain('Ready to cite: 2 documents (1 documented, 1 implemented, 1 stale).')
      const policies = screen.getByRole('table', { name: 'Policies' })
      expect(within(policies).getAllByRole('rowheader').map((r) => r.querySelector('.doc-title')?.textContent))
        .toEqual(['Backup policy'])
      const records = screen.getByRole('table', { name: 'Records' })
      expect(within(records).getAllByRole('rowheader')).toHaveLength(1)
      expect(screen.queryAllByRole('checkbox')).toEqual([])
      expect(screen.getByText('Creating the company adds it to the profile switcher. Then Vouch sets its Goal and runs '
        + 'a profile pass: every applicable control drafted, waiting for your review.')).toBeTruthy()
      expect(createButton().getAttribute('type')).toBe('submit')
    })

  it('step 7 says None with no control marked, and leaves out a folder with nothing chosen', async () => {
    api()
    wizard()
    await toStep6()
    await userEvent.click(await findChoose('Backup policy'))
    await next()
    const summary = heading('Finish').parentElement?.querySelector('dl') as HTMLElement
    expect(within(summary).getAllByRole('definition')[5].textContent).toBe('None')
    expect(within(summary).getAllByRole('definition')[2].textContent).toBe('English')
    expect(screen.getByRole('table', { name: 'Policies' })).toBeTruthy()
    expect(screen.queryByRole('table', { name: 'Records' })).toBeNull()
  })

  it('Create company sends the answers in the CompanySetup shape, the name and each reason trimmed, nothing more', async () => {
    const fetch = api()
    wizard()
    await toStep7()
    await create()
    const [sent] = posts(fetch)
    expect(sent[1]?.method).toBe('POST')
    expect(JSON.parse(String(sent[1]?.body))).toEqual({
      company: 'Musterfirma GmbH', country: 'Germany', answer_language: 'de', protection_level: 'high',
      not_applicable: [{ control: '5.1.1', reason: 'No cryptography' }, { control: '1.6.1', reason: 'Remote only' }],
      evidence: { policies: ['backup-policy'], records: ['training-record'] },
    })
  })

  it('a folder with nothing chosen is sent as an empty list', async () => {
    const fetch = api()
    wizard()
    await toStep6()
    await userEvent.click(await findChoose('Security training record 2026'))
    await next()
    await create()
    const body = JSON.parse(String(posts(fetch)[0][1]?.body)) as { evidence: unknown; not_applicable: unknown }
    expect(body.evidence).toEqual({ policies: [], records: ['training-record'] })
    expect(body.not_applicable).toEqual([])
  })

  it('a 201 hands the created profile and the chosen Goal to onCreated', async () => {
    api()
    const onCreated = wizard()
    await toStep7()
    await create()
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED, 'internal-improvement'))
    expect(onCreated).toHaveBeenCalledOnce()
  })

  it('while the create is out, Create company says Creating… and it and Back are disabled; a double press sends one',
    async () => {
      const fetch = api()
      let answer: (r: Response) => void = () => {}
      fetch.mockImplementation((url: string) => url === PROFILES_PATH
        ? new Promise<Response>((resolve) => { answer = resolve })
        : Promise.resolve(url === CONTROLS_PATH ? controlsAnswer() : url === LIBRARY_PATH ? libraryAnswer() : levelsAnswer()))
      const onCreated = wizard()
      await toStep7()
      clock += 1000
      const button = createButton()
      await userEvent.dblClick(button)
      expect(button.textContent).toBe('Creating…')
      expect((button as HTMLButtonElement).disabled).toBe(true)
      expect(button.getAttribute('aria-busy')).toBe('true')
      expect((backButton() as HTMLButtonElement).disabled).toBe(true)
      fireEvent.submit(button.closest('form') as HTMLFormElement) // Enter in the form while it is out
      expect(posts(fetch)).toHaveLength(1)
      answer(createdAnswer())
      await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
    })

  it('a press within 400 ms of the move to step 7 sends nothing: a double-click on step 6\'s Next', async () => {
    const fetch = api()
    wizard()
    await toStep6()
    await userEvent.click(await findChoose('Backup policy'))
    await next()
    await userEvent.click(createButton()) // the same moment: the double-click's second press
    clock += 399
    await userEvent.click(createButton())
    expect(posts(fetch)).toHaveLength(0)
    expect(createButton().textContent).toBe('Create company')
    clock += 1
    await userEvent.click(createButton())
    expect(posts(fetch)).toHaveLength(1)
  })

  it.each([
    ['a string detail', Response.json({ detail: 'evidence.policies: no document "x"' }, { status: 422 }),
      'Vouch could not create this company: evidence.policies: no document "x".'],
    ['a list', Response.json({ detail: [{ loc: ['body', 'company'], msg: 'String should have at most 100 characters' }] },
      { status: 422 }), 'Vouch could not create this company: String should have at most 100 characters.'],
    ['a detail ending in a period', Response.json({ detail: 'No such document.' }, { status: 422 }),
      'Vouch could not create this company: No such document.'],
    ['no detail', Response.json({}, { status: 422 }), 'Vouch could not create this company.'],
    ['a blank detail', Response.json({ detail: '  ' }, { status: 422 }), 'Vouch could not create this company.'],
  ])('a 422 with %s stays on step 7 and says what was refused, every answer kept', async (_, refusal, words) => {
    const fetch = api([], [], [], [refusal])
    const onCreated = wizard()
    await toStep7()
    await create()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(words)
    expect(alert.className).toContain('setup-alert')
    expect(heading('Finish')).toBeTruthy()
    expect(screen.getByText('Musterfirma GmbH')).toBeTruthy()
    expect(createButton().textContent).toBe('Create company')
    expect((createButton() as HTMLButtonElement).disabled).toBe(false)
    expect((backButton() as HTMLButtonElement).disabled).toBe(false)
    expect(onCreated).not.toHaveBeenCalled()
    await back()
    expect((choose('Backup policy') as HTMLInputElement).checked).toBe(true)
    await back()
    expect((reason('1.6.1') as HTMLTextAreaElement).value).toBe('  Remote only  ')
    await next()
    await next()
    expect(screen.queryByRole('alert')).toBeNull() // leaving the step drops it
    await create()
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED, 'internal-improvement'))
    expect(posts(fetch)).toHaveLength(2)
  })

  it.each([
    ['a 503', Response.json({ detail: 'down', unavailable: 'database' }, { status: 503 }) as Answer,
      'The database is unavailable just now. Nothing was saved; try again in a moment.'],
    ['a 403', new Response('{}', { status: 403 }) as Answer, 'The company could not be created. Nothing was saved.'],
  ])('%s says nothing was saved, and Create company can be pressed again and succeeds', async (_, failure, words) => {
    const fetch = api([], [], [], [failure])
    const onCreated = wizard()
    await toStep7()
    await create()
    expect((await screen.findByRole('alert')).textContent).toBe(words)
    expect(onCreated).not.toHaveBeenCalled()
    await create()
    expect(screen.queryByRole('alert')).toBeNull() // a new press clears the last alert
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED, 'internal-improvement'))
    expect(posts(fetch)).toHaveLength(2)
  })

  const LOST: [string, () => Answer][] = [
    ['a 500', () => new Response('{}', { status: 500 })],
    ['an unreachable server', () => 'offline'],
    ["a 201 whose body won't parse", () => new Response('<html>', { status: 201 })],
  ]

  it.each(LOST)('after %s, a company of that name newly listed was created: onCreated has it', async (_, lost) => {
    const fetch = api([], [], [], [lost()], [listing(), listing(CREATED)])
    const onCreated = wizard()
    await toStep7()
    await create()
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED, 'internal-improvement'))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(posts(fetch)).toHaveLength(1)
  })

  it.each(LOST)("after %s, with the company not listed, says it may have been made; nothing says it wasn't",
    async (_, lost) => {
      api([], [], [], [lost()])
      const onCreated = wizard()
      await toStep7()
      await create()
      expect((await screen.findByRole('alert')).textContent)
        .toBe('The company may not have been created. Check the profile switcher before trying again.')
      expect(onCreated).not.toHaveBeenCalled()
      expect(document.activeElement).toBe(createButton())
    })

  it('a 401 signs out and shows no alert', async () => {
    api([], [], [], [new Response(null, { status: 401 })])
    const signOut = vi.fn()
    setOnSignedOut(signOut)
    const onCreated = wizard()
    await toStep7()
    await create()
    await waitFor(() => expect(signOut).toHaveBeenCalledOnce())
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onCreated).not.toHaveBeenCalled()
  })
})
