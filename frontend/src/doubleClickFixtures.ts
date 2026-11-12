import { vi } from 'vitest'
import { DOUBLE_CLICK_MS } from './doubleClickGuard.ts'

/** Test helper (#527), shared by the InlineConfirm, Dossier, profiles and App tests: a person's next click comes after
 * a double-click's would, as after a pointer click on the in-app ask's confirm or the chat's Close (#333) every click
 * within `DOUBLE_CLICK_MS` of the one before presses nothing (#562). Under fake timers it moves the clock on;
 * otherwise it waits. */
export async function pastDoubleClick(): Promise<void> {
  if (vi.isFakeTimers()) vi.advanceTimersByTime(DOUBLE_CLICK_MS)
  else await new Promise((done) => setTimeout(done, DOUBLE_CLICK_MS))
}
