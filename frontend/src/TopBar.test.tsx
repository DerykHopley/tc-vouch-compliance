import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import TopBar from './TopBar.tsx'

afterEach(cleanup)

/** The words beside Vouch's name. */
const label = () => screen.getByText('Vouch').nextElementSibling as HTMLElement

describe("the top bar's label in each goal (ticket 440)", () => {
  it('speaks of audit preparation with no profile read yet', () => {
    render(<TopBar />)
    expect(label().textContent).toBe('Audit preparation')
  })

  it.each(['en', 'de'])('speaks of audit preparation under Audit readiness (%s), in English as before', (language) => {
    render(<TopBar goal="audit-readiness" language={language} />)
    expect(label().textContent).toBe('Audit preparation')
    expect(label().getAttribute('lang')).toBeNull()
  })

  it('speaks of security improvement under Internal improvement', () => {
    render(<TopBar goal="internal-improvement" language="en" />)
    expect(label().textContent).toBe('Security improvement')
    expect(label().getAttribute('lang')).toBeNull()
  })

  it('speaks of security improvement in German under Internal improvement for a German profile, marked German', () => {
    render(<TopBar goal="internal-improvement" language="de" />)
    expect(label().textContent).toBe('Sicherheitsverbesserung')
    expect(label().getAttribute('lang')).toBe('de')
  })
})

const NORDLICHT = { id: 'nordlicht', company: 'Nordlicht Komponenten GmbH', country: 'Germany', answer_language: 'en' }

describe('the top bar names the current company (ticket 495, option A "Letterhead")', () => {
  it('reads Vouch, the Goal word, the rule, the company and its tag, in order, the name neither a button nor a link', () => {
    render(<TopBar goal="audit-readiness" language="en" company={NORDLICHT} />)
    const bar = screen.getByRole('banner')
    const rule = bar.querySelector('.top-rule')
    expect(rule?.getAttribute('aria-hidden')).toBe('true')
    const name = screen.getByText('Nordlicht Komponenten GmbH')
    const tag = screen.getByText('DE · EN')
    const order = [screen.getByText('Vouch'), screen.getByText('Audit preparation'), rule!, name, tag]
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(name.closest('button, a')).toBeNull()
    expect(tag.closest('button, a')).toBeNull()
    expect(screen.queryAllByRole('button')).toEqual([])
    expect(screen.queryAllByRole('link')).toEqual([])
  })

  it('shows neither the rule nor a name without a company', () => {
    render(<TopBar goal="audit-readiness" language="en" />)
    const bar = screen.getByRole('banner')
    expect(bar.querySelector('.top-rule')).toBeNull()
    expect(bar.querySelector('.top-company')).toBeNull()
  })

  it("tags a country with no ISO code by its name, from the overview's fields alone", () => {
    render(<TopBar company={{ company: 'Testwerk GmbH', country: 'Somewhere', answer_language: 'de' }} />)
    expect(screen.getByText('Somewhere · DE')).toBeTruthy()
  })
})
