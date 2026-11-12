/**
 * Company setup's history entries (#476, #568): once a create from a wizard has landed, neither Back nor Forward
 * reaches that wizard again, empty, under the new company. Each wizard marks its own entry (its history state carries
 * an id), so a create gives way only its own wizard's entry, wherever the developer has gone meanwhile: still on it,
 * it is replaced now; left by Back or a tab click, it is replaced when Back or Forward reaches it (settledPath). The
 * finished entries are held for this page's life only: after a reload, a wizard reached by Back is empty again.
 */

type SetupState = { setup: string }

let made = 0
const finished = new Map<string, string>() // a finished setup entry's id, and the path it gives way to

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
 * entry; otherwise the developer stays where they went, and the entry gives way once Back or Forward reaches it. */
export function leaveSetup(entry: string, path: string) {
  if (idOf(window.history.state) === entry) window.history.replaceState(null, '', path)
  else finished.set(entry, path)
}

/** After Back or Forward: the path to show. A finished setup's entry is first replaced by the path it gives way to. */
export function settledPath(): string {
  const entry = idOf(window.history.state)
  const path = entry === null ? undefined : finished.get(entry)
  if (path !== undefined) {
    finished.delete(entry!)
    window.history.replaceState(null, '', path)
  }
  return window.location.pathname
}
