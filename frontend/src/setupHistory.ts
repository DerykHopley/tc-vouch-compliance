/**
 * Company setup's history entries (#476, #568): once a create from a wizard has landed, neither Back nor Forward
 * reaches that wizard again, empty, under the new company. Each wizard marks its own entry (its history state carries
 * an id), so a create gives way only its own wizard's entry, wherever the developer has gone meanwhile: still on it,
 * it is replaced now; left by Back or a tab click, it is replaced when Back or Forward reaches it (settleSetupEntry). The
 * finished entries are held for this page's life only: after a reload, a wizard reached by Back is empty again.
 */

type SetupState = { setup: string }

let made = 0
// A finished setup entry's id: the path it gives way to, and what to do once Back or Forward reaches it (#569).
const finished = new Map<string, { path: string; reached?: () => void }>()

const idOf = (state: unknown): string | null =>
  typeof state === 'object' && state !== null && typeof (state as SetupState).setup === 'string'
    ? (state as SetupState).setup : null

/** Marks the history entry shown as a setup's, once: its id, kept as the wizard opens again on it. */
export function markSetupEntry(): string {
  const marked = idOf(window.history.state)
  if (marked !== null) return marked
  const id = `${Date.now()}-${++made}`
  window.history.replaceState({ setup: id } satisfies SetupState, '')
  return id
}

/** A create from setup entry `entry` landed: that entry gives way to `path`. Shown, it is replaced now, with no new
 * entry, and this is true; otherwise the developer stays where they went (false), and the entry gives way once Back or
 * Forward reaches it, then `reached` runs, once (#569: the company made opens there). */
export function leaveSetup(entry: string, path: string, reached?: () => void): boolean {
  if (idOf(window.history.state) !== entry) {
    finished.set(entry, { path, reached })
    return false
  }
  window.history.replaceState(null, '', path)
  return true
}

/** After Back or Forward: a finished setup's entry, when Back or Forward reached one, is replaced by the path it gives
 * way to (and forgotten); then the path to show. */
export function settleSetupEntry(): string {
  const entry = idOf(window.history.state)
  const given = entry === null ? undefined : finished.get(entry)
  if (given !== undefined) {
    finished.delete(entry!)
    window.history.replaceState(null, '', given.path)
    given.reached?.()
  }
  return window.location.pathname
}

/** Tests only: forgets every finished entry and restarts the ids, so each test starts as a fresh page. */
export function forgetSetupEntries() {
  made = 0
  finished.clear()
}
