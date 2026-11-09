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

const levelsAnswer = () => new Response(JSON.stringify({ levels: LEVELS }))

/** fetch answering the scope read with `answers` in turn (a Response, or 'offline' to reject). */
function api(...answers: (Response | 'offline')[]) {
  const fetch = vi.fn(() => {
    const next = answers.shift() ?? levelsAnswer()
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

describe('company setup', () => {
  it('lists all seven steps, marks the current one, and starts on step 1 with no Back', () => {
    api()
    render(<CompanySetupWizard developerPath="/developer" onDeveloper={() => {}} />)
    const steps = within(screen.getByRole('list', { name: 'Setup steps' })).getAllByRole('listitem')
    expect(steps.map((s) => s.textContent)).toEqual([
      '1Company', '2Language', '3Protection level', '4Goal (not yet available)', '5Not applicable (not yet available)',
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

  it("step 3 shows each level's scope from the route, offers no Next, and never posts a profile", async () => {
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
    expect(screen.queryByRole('button', { name: 'Next' })).toBeNull()
    expect(screen.getByText('The next steps (Goal, not applicable, evidence, finish) are not built yet. Nothing has been saved.'))
      .toBeTruthy()
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
