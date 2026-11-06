/** The one user of local mode, who signs in nowhere (the API's default name, vouch.settings.DEFAULT_USER_NAME). */
export const LOCAL_USER = 'Local user'

/** A name's initials: the first letters of its first and last words ("Anna Maria Berg": "AB"). */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  const first = words[0][0]
  const last = words.length > 1 ? words[words.length - 1][0] : ''
  return (first + last).toUpperCase()
}
