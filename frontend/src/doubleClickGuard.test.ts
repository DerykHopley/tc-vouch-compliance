import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from 'vitest'
import { DOUBLE_CLICK_MS, swallowPointerClickBurst } from './doubleClickGuard.ts'

/** #562: the guard (#527, #333) swallows every pointer click of the burst that follows a pointer click on a control
 * that leaves the page, not just the next one: a triple click's third click landed on the page behind. A burst ends
 * after `DOUBLE_CLICK_MS` with no click. */
describe('the click-burst guard (ticket 562)', () => {
  let behind: HTMLButtonElement
  let pressed: Mock<(e: Event) => void>
  const click = (detail: number) => behind.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail }))

  beforeEach(() => {
    vi.useFakeTimers()
    pressed = vi.fn<(e: Event) => void>()
    behind = document.createElement('button')
    behind.addEventListener('click', pressed)
    document.body.append(behind)
  })
  afterEach(() => {
    behind.remove()
    vi.useRealTimers()
  })

  it('swallows every click of a triple click, not just the second', () => {
    swallowPointerClickBurst({ detail: 1 })
    click(2)
    click(3)
    expect(pressed).not.toHaveBeenCalled()
  })

  it('keeps the burst going while each click comes within DOUBLE_CLICK_MS of the last', () => {
    swallowPointerClickBurst({ detail: 1 })
    for (let i = 2; i <= 5; i++) {
      vi.advanceTimersByTime(DOUBLE_CLICK_MS - 1)
      click(i)
    }
    expect(pressed).not.toHaveBeenCalled()
  })

  it('lets the first click after a DOUBLE_CLICK_MS pause through, and the guard is gone', () => {
    swallowPointerClickBurst({ detail: 1 })
    click(2)
    vi.advanceTimersByTime(DOUBLE_CLICK_MS)
    click(1)
    click(2)
    expect(pressed).toHaveBeenCalledTimes(2)
  })

  it('runs onSwallowed on each swallowed click', () => {
    const onSwallowed = vi.fn()
    swallowPointerClickBurst({ detail: 1 }, onSwallowed)
    click(2)
    click(3)
    expect(onSwallowed).toHaveBeenCalledTimes(2)
  })

  it('cancels each press of the burst, so it neither selects a word nor moves focus (ticket 563)', () => {
    const press = () => behind.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, detail: 2 }))
    swallowPointerClickBurst({ detail: 1 })
    expect(press()).toBe(false)
    vi.advanceTimersByTime(DOUBLE_CLICK_MS)
    expect(press()).toBe(true)
  })

  it('never swallows a keyboard click (detail 0), and a keyboard click sets no guard', () => {
    swallowPointerClickBurst({ detail: 1 })
    click(0)
    expect(pressed).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(DOUBLE_CLICK_MS)
    swallowPointerClickBurst({ detail: 0 })
    click(1)
    expect(pressed).toHaveBeenCalledTimes(2)
  })
})
