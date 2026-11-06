// What this browser keeps between visits (localStorage). Storage can be missing or refused (a private window, blocked
// site data): then a read finds nothing and a write keeps nothing, and the caller carries on with its default.

/** The value kept under `key`, or null. */
export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

/** Keeps `value` under `key`; null forgets it. */
export function store(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // nothing kept
  }
}
