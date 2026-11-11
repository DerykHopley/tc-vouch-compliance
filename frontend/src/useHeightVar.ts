import { type RefObject, useLayoutEffect } from 'react'

/** The element's height: its whole box (`border`), or inside its padding and rules (`content`). */
function heightOf(el: HTMLElement, box: 'border' | 'content') {
  const height = el.getBoundingClientRect().height
  if (box === 'border') return height
  const style = getComputedStyle(el)
  const sides = ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth'] as const
  return height - sides.reduce((sum, side) => sum + (parseFloat(style[side]) || 0), 0)
}

/**
 * Keeps the element's height (its `box`) in the CSS variable `name` on the page (the root element), while `active`, as
 * the element grows or shrinks; the variable goes once the element does, or `active` turns false. The top bar's
 * `--top-bar` (#205) and the review's docked buttons' `--review-actions` (#467) are kept this way, for app.css's
 * scroll padding and scroll margins.
 */
export function useHeightVar(ref: RefObject<HTMLElement | null>, name: string, active = true,
  box: 'border' | 'content' = 'border') {
  useLayoutEffect(() => {
    const el = ref.current
    if (!active || !el) return
    const root = document.documentElement
    const measure = () => root.style.setProperty(name, `${heightOf(el, box)}px`)
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(el)
    return () => {
      observer?.disconnect()
      root.style.removeProperty(name)
    }
  }, [ref, name, active, box])
}
