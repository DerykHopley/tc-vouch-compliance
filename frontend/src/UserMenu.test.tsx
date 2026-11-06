import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import UserMenu from './UserMenu.tsx'

afterEach(cleanup)

describe('UserMenu (issue 222)', () => {
  it("shows the user's initials, local mode's one user as LU", () => {
    render(<UserMenu name="Anna Berg" theme="light" onTheme={() => {}} onSignOut={() => {}} />)
    expect(screen.getByRole('button', { name: 'Anna Berg: your menu' }).textContent).toBe('AB')
    cleanup()
    render(<UserMenu theme="light" onTheme={() => {}} />)
    expect(screen.getByRole('button', { name: 'Your menu' }).textContent).toBe('LU')
  })

  it("shows an ellipsis, never local mode's LU, while Clerk has not read the user yet", () => {
    render(<UserMenu theme="light" onTheme={() => {}} onSignOut={() => {}} />)
    expect(screen.getByRole('button', { name: 'Your menu' }).textContent).toBe('…')
  })
})
