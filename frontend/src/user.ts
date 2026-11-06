/** The app's colour theme (App.tsx, the user menu). */
export type Theme = 'light' | 'dark'

/** The one user of local mode, who signs in nowhere (the API's default name, vouch.settings.DEFAULT_USER_NAME). */
export const LOCAL_USER = 'Local user'

/** The user menu's name for a Clerk user: the full name, else the username, else the address they signed in with.
 * Clerk gives an empty full name, not null, to a user with no first or last name (a Discord username, say). */
export function displayName(user: {
  fullName: string | null
  username: string | null
  primaryEmailAddress: { emailAddress: string } | null
} | null | undefined): string | undefined {
  return user?.fullName || user?.username || user?.primaryEmailAddress?.emailAddress || undefined
}

/** A name's initials: the first letters of its first and last words ("Anna Maria Berg": "AB"). */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  const first = words[0][0]
  const last = words.length > 1 ? words[words.length - 1][0] : ''
  return (first + last).toUpperCase()
}
