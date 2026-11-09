import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CompanySetupWizard from './CompanySetupWizard.tsx'
import type { ProtectionScope } from './setup.ts'

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

const levelsAnswer = () => new Response(JSON.stringify({ levels: LEVELS }))
const controlsAnswer = () => new Response(JSON.stringify({ controls: CONTROLS }))

type Answer = Response | 'offline'

/** fetch answering the scope read with `levels` in turn and the controls read with `controls` in turn (a Response, or
 * 'offline' to reject), then with the full answer. */
function api(levels: Answer[] = [], controls: Answer[] = []) {
  const fetch = vi.fn((url: string) => {
    const reading = url === '/api/developer/controls'
    const next = (reading ? controls.shift() : levels.shift()) ?? (reading ? controlsAnswer() : levelsAnswer())
    return next === 'offline' ? Promise.reject(new TypeError('offline')) : Promise.resolve(next)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const next = () => userEvent.click(screen.getByRole('button', { name: 'Next' }))
const back = () => userEvent.click(screen.getByRole('button', { name: 'Back' }))
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

describe('company setup', () => {
  it('lists all seven steps, marks the current one, and starts on step 1 with no Back', () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps.map((s) => s.textContent)).toEqual([
      '1Company', '2Language', '3Protection level', '4Goal (not yet available)', '5Not applicable',
      '6Evidence (not yet available)', '7Finish (not yet available)'])
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
    expect(screen.getByText('The next steps (evidence, finish) are not built yet. Nothing has been saved.')).toBeTruthy()
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
    expect(screen.getByText('At least one control must apply.')).toBeTruthy()
    expect(document.activeElement).toBe(control(CONTROLS[0].id))
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
    await next() // Next still works with nothing marked
    expect(screen.queryByText('At least one control must apply.')).toBeNull()
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
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(['/api/developer/protection-levels', '/api/developer/controls'])
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
