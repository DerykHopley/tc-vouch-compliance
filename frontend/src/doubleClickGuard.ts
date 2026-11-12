/** The longest common double-click interval: how long a pause ends a click burst the guard swallows (#527, #562). */
export const DOUBLE_CLICK_MS = 500

/** Takes away the double-click guard, if one is set (one at a time, page-wide). */
let endGuard: (() => void) | null = null

/**
 * After `click`, a pointer click on a control that leaves the page or collapses, swallows every pointer click of the
 * same burst anywhere on the page (#527, #562): a double or triple click's later clicks would land on whatever moves up
 * under the pointer. The burst lasts while each click comes within `DOUBLE_CLICK_MS` of the one before; the first click
 * after a pause that long goes through. A keyboard click (`detail === 0`) neither sets the guard nor is swallowed. The
 * guard outlives the control, which can leave the page as its click takes effect (Close round's, as the round closes)
 * with something else in its place. `onSwallowed`, if given, runs after each swallowed click: its press may already
 * have moved focus. Shared by the in-app ask's confirm and the chat's Close (#333).
 */
export function swallowPointerClickBurst(click: { detail: number }, onSwallowed?: () => void) {
  if (click.detail === 0) return
  endDoubleClickGuard()
  let timer = window.setTimeout(endDoubleClickGuard, DOUBLE_CLICK_MS)
  const swallow = (e: MouseEvent) => {
    if (e.detail === 0) return
    e.preventDefault()
    e.stopPropagation()
    window.clearTimeout(timer)
    timer = window.setTimeout(endDoubleClickGuard, DOUBLE_CLICK_MS)
    onSwallowed?.()
  }
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
