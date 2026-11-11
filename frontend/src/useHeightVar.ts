import { type RefObject, useLayoutEffect } from 'react'

/**
 * Keeps the element's height in the CSS variable `name` on the page (the root element), while `active`, as the
 * element grows or shrinks; the variable goes once the element does, or `active` turns false. The top bar's
 * `--top-bar` (#205) and the review bar's `--review-actions` (#467) are kept this way, for app.css's scroll padding
 * and scroll margins.
 */
export function useHeightVar(ref: RefObject<HTMLElement | null>, name: string, active = true) {
  useLayoutEffect(() => {
    const el = ref.current
    if (!active || !el) return
    const root = document.documentElement
    const measure = () => root.style.setProperty(name, `${el.getBoundingClientRect().height}px`)
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(el)
    return () => {
      observer?.disconnect()
      root.style.removeProperty(name)
    }
  }, [ref, name, active])
}
