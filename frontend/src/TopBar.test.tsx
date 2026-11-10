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
