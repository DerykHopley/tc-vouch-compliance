import { type ReactNode, useLayoutEffect, useRef } from 'react'

/** The top bar on every page: Vouch's name, then whatever the page puts beside it. Its height, which grows at phone
 * width, is kept in `--top-bar` on the page (app.css: scroll-padding-top), so a field brought into view by focus or
 * by scrolling to it stops below the sticky bar, not under it (#205). */
export default function TopBar({ children }: { children?: ReactNode }) {
  const bar = useRef<HTMLElement>(null)
  useLayoutEffect(() => {
    const el = bar.current
    if (!el) return
    const root = document.documentElement
    const measure = () => root.style.setProperty('--top-bar', `${el.getBoundingClientRect().height}px`)
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(el)
    return () => {
      observer?.disconnect()
      root.style.removeProperty('--top-bar')
    }
  }, [])
  return (
    <header ref={bar} className="top">
      <div className="top-in wrap">
        <div className="brand"><b>Vouch</b><span>Audit preparation</span></div>
        {children}
      </div>
    </header>
  )
}
