/** The longest common double-click interval: how long after a pointer confirm the next click presses nothing (#527). */
export const DOUBLE_CLICK_MS = 500

/** Takes away the double-click guard, if one is set (one at a time, page-wide). */
let endGuard: (() => void) | null = null

/**
 * After `click`, a pointer click on a control that leaves the page or collapses, swallows the next pointer click
 * anywhere on the page, if it comes within `DOUBLE_CLICK_MS` (#527): a double-click's second click would land on
 * whatever moves up under the pointer. A keyboard click (`detail === 0`) neither sets the guard nor is swallowed. The
 * guard outlives the control, which can leave the page as its click takes effect (Close round's, as the round
 * closes) with something else in its place. It goes by itself, on that click or after `DOUBLE_CLICK_MS`.
 * `onSwallowed`, if given, runs after a click is swallowed: the second click's press may already have moved focus.
 */
export function swallowNextPointerClick(click: { detail: number }, onSwallowed?: () => void) {
  if (click.detail === 0) return
  endDoubleClickGuard()
  const swallow = (e: MouseEvent) => {
    if (e.detail === 0) return
    e.preventDefault()
    e.stopPropagation()
    endDoubleClickGuard()
    onSwallowed?.()
  }
  const timer = window.setTimeout(endDoubleClickGuard, DOUBLE_CLICK_MS)
  endGuard = () => {
    window.clearTimeout(timer)
    document.removeEventListener('click', swallow, true)
  }
  document.addEventListener('click', swallow, true)
}

/** Ends the double-click guard now: for the unit tests, between tests sharing one document (`testSetup.ts`). */
export function endDoubleClickGuard() {
  endGuard?.()
  endGuard = null
}
