import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanySetupWizard from './CompanySetupWizard.tsx'
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
  stale_because: null, stale: false, ...more,
})
/** The sample library as GET /api/developer/sample-library sends it: two policies (one stale), then a record. */
const LIBRARY: LibraryDocument[] = [
  libraryDocument('access-rights-policy', 'Access rights policy'),
  libraryDocument('backup-policy', 'Backup policy', {
    approval_date: '2024-02-12', stale_because: 'approved 2024-02-12 with a yearly review, due 2025-02-12', stale: true,
  }),
  libraryDocument('training-record', 'Security training record 2026', { source: 'records', kind: 'implemented' }),
]

const levelsAnswer = () => new Response(JSON.stringify({ levels: LEVELS }))
const controlsAnswer = () => new Response(JSON.stringify({ controls: CONTROLS }))
const libraryAnswer = () => new Response(JSON.stringify({ documents: LIBRARY }))
const CONTROLS_PATH = '/api/developer/controls'
const LIBRARY_PATH = '/api/developer/sample-library'

type Answer = Response | 'offline'

/** fetch answering the scope read with `levels` in turn, the controls read with `controls` in turn and the library
 * read with `library` in turn (a Response, or 'offline' to reject), then with the full answer. */
function api(levels: Answer[] = [], controls: Answer[] = [], library: Answer[] = []) {
  const fetch = vi.fn((url: string) => {
    const next = url === CONTROLS_PATH ? controls.shift() ?? controlsAnswer()
      : url === LIBRARY_PATH ? library.shift() ?? libraryAnswer()
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
const NOT_BUILT = 'The finish is not built yet. Nothing has been saved.'

describe('company setup', () => {
  it('lists all seven steps, marks the current one, and starts on step 1 with no Back', () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps.map((s) => s.textContent)).toEqual([
      '1Company', '2Language', '3Protection level', '4Goal (not yet available)', '5Not applicable',
      '6Evidence', '7Finish (not yet available)'])
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await stepOne('Musterfirma GmbH')
    await userEvent.type(nameField(), '{Enter}')
    expect(heading('Language')).toBeTruthy()
  })

  it('moves focus to each new step heading, and marks it current in the step list', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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

  it('Next on step 3 with a level skips the Goal to step 5, and Back returns to step 3', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await toStep5()
    expect(screen.getByText('Step 5 of 7')).toBeTruthy()
    expect(document.activeElement).toBe(heading('Not applicable'))
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps[3].textContent).toBe('4Goal (not yet available)')
    expect(steps[3].className).toBe('later')
    expect(steps.map((s) => s.getAttribute('aria-current'))).toEqual([null, null, null, null, 'step', null, null])
    await back()
    expect(heading('Protection level')).toBeTruthy()
    expect(screen.getByText('Step 3 of 7')).toBeTruthy()
  })

  it('step 5 lists every control unticked, under the sentence that says what not applicable means', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await toStep5()
    expect((await screen.findByRole('alert')).textContent).toContain('The controls could not be loaded.')
  })

  it('reads the controls once, and posts no profile at any step', async () => {
    const fetch = api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await toStep3()
    await next()
    expect(screen.getByText('Choose a protection level.')).toBeTruthy()
    await back()
    await next()
    expect(screen.queryByText('Choose a protection level.')).toBeNull()
    expect(screen.getByRole('group', { name: 'Protection level' }).getAttribute('aria-invalid')).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: /^Normal/ }))
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await toStep5()
    await userEvent.click(await findControl('1.6.1'))
    await userEvent.type(reason('1.6.1'), ' No prototype vehicles ')
    await next()
    expect(screen.getByText('Step 6 of 7')).toBeTruthy()
    expect(document.activeElement).toBe(heading('Evidence'))
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps[5].textContent).toBe('6Evidence')
    expect(steps[5].className).toBe('')
    expect(steps.map((s) => s.getAttribute('aria-current'))).toEqual([null, null, null, null, null, 'step', null])
    await back()
    expect((reason('1.6.1') as HTMLTextAreaElement).value).toBe(' No prototype vehicles ')
  })

  it('step 6 lists the library under Policies and Records, none chosen, under the sentence that says what it is for', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    expect(screen.queryByText(NOT_BUILT)).toBeTruthy()
  })

  it('the ready-to-cite line follows each tick', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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

  it('Next on step 6 with a document chosen stays and focuses the note that the finish is not built yet', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await toStep6()
    await userEvent.click(await findChoose('Backup policy'))
    await next()
    expect(heading('Evidence')).toBeTruthy()
    expect(screen.queryByText('Choose at least one document.')).toBeNull()
    expect(document.activeElement).toBe(screen.getByText(NOT_BUILT))
  })

  it('the note that the finish is not built yet shows on no other step', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    const seen = [screen.queryByText(NOT_BUILT)]
    await stepOne()
    await next()
    seen.push(screen.queryByText(NOT_BUILT))
    await next()
    seen.push(screen.queryByText(NOT_BUILT))
    await userEvent.click(await screen.findByRole('radio', { name: /^Normal/ }))
    await next()
    seen.push(screen.queryByText(NOT_BUILT))
    expect(seen).toEqual([null, null, null, null])
    await findControl('1.1.1')
    await next()
    expect(screen.getByText(NOT_BUILT)).toBeTruthy()
  })

  it('keeps the chosen documents through Back and Next', async () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await toStep6()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('The sample library could not be loaded.')
    expect(screen.queryAllByRole('checkbox')).toEqual([])
    await next() // nothing loaded: stays and says so, focus on Try again, as there is no checkbox
    expect(heading('Evidence')).toBeTruthy()
    expect(screen.getByText('Choose at least one document.')).toBeTruthy()
    expect(document.activeElement).toBe(within(alert).getByRole('button', { name: 'Try again' }))
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await findChoose('Backup policy')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(fetch.mock.calls.filter((c) => c[0] === LIBRARY_PATH)).toHaveLength(2)
  })

  it('says while the library loads', async () => {
    const fetch = api()
    fetch.mockImplementation((url: string) => url === LIBRARY_PATH ? new Promise<Response>(() => {})
      : Promise.resolve(url === CONTROLS_PATH ? controlsAnswer() : levelsAnswer()))
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    await toStep6()
    expect(screen.getByText('Loading the sample library…')).toBeTruthy()
  })

  it('reads the library once across Back and Next, and posts no profile at any step', async () => {
    const fetch = api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
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
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={onDeveloper} />)
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(crumbs.textContent).toBe('Developer›Company setup')
    expect(within(crumbs).getByRole('link', { name: 'Developer' }).getAttribute('href')).toBe('/developer')
    await userEvent.click(within(crumbs).getByRole('link', { name: 'Developer' }))
    expect(onDeveloper).toHaveBeenCalled()
  })
})
