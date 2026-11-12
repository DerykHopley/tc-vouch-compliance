import { type RefObject, useEffect, useState } from 'react'

/**
 * Whether more of the scrolling `box` lies beyond its far edge on `axis` (below its foot for `y`, beyond its right edge
 * for `x`), kept as the box scrolls and as it or its `content` resizes. The caller marks the box with `data-more`,
 * which app.css fades: the working paper's stacked index (#555) and the document tables (#607).
 */
export function useScrollMore(box: RefObject<HTMLElement | null>, content: RefObject<HTMLElement | null>,
  axis: 'x' | 'y'): boolean {
  const [more, setMore] = useState(false)
  useEffect(() => {
    const el = box.current
    if (!el) return
    const measure = () => setMore(axis === 'y'
      ? el.scrollHeight - el.scrollTop - el.clientHeight > 1
      : el.scrollWidth - el.scrollLeft - el.clientWidth > 1)
    measure()
    el.addEventListener('scroll', measure, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(el)
    if (content.current) observer?.observe(content.current)
    return () => {
      el.removeEventListener('scroll', measure)
      observer?.disconnect()
    }
  }, [box, content, axis])
  return more
}
