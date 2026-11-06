import { describe, expect, it } from 'vitest'
import { displayName, initials } from './user.ts'

describe('displayName (issue 222)', () => {
  const user = (fullName: string | null, username: string | null, email: string | null = null) =>
    ({ fullName, username, primaryEmailAddress: email ? { emailAddress: email } : null })

  it("is Clerk's full name, else the username, else the address, an empty full name counting as none", () => {
    expect(displayName(user('Anna Berg', 'anna'))).toBe('Anna Berg')
    expect(displayName(user('', 'anna_discord'))).toBe('anna_discord')
    expect(displayName(user(null, null, 'anna@example.com'))).toBe('anna@example.com')
    expect(displayName(user('', '', null))).toBeUndefined()
    expect(displayName(undefined)).toBeUndefined()
  })
})

describe('initials', () => {
  it('takes the first and last words', () => {
    expect(initials('Anna Maria Berg')).toBe('AB')
    expect(initials('anna_discord')).toBe('A')
    expect(initials('Local user')).toBe('LU')
  })
})
