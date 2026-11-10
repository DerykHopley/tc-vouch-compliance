import { type ReactNode, useLayoutEffect, useRef } from 'react'
import { goalWording } from './overview.ts'
import type { Goal } from './report.ts'

/** The top bar on every page: Vouch's name and what it is for in the profile's goal (#440: `goal` and `language`, the
 * profile's answer language, once the profile is read), then whatever the page puts beside it. Its height, which grows
 * at phone width, is kept in `--top-bar` on the page (app.css: scroll-padding-top), so a field brought into view by
 * focus or by scrolling to it stops below the sticky bar, not under it (#205). */
export default function TopBar({ children, goal, language }: { children?: ReactNode; goal?: Goal; language?: string }) {
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
  const { label, lang } = goalWording(goal, language)
  return (
    <header ref={bar} className="top">
      <div className="top-in wrap">
        <div className="brand"><b>Vouch</b><span lang={lang}>{label}</span></div>
        {children}
      </div>
    </header>
  )
}
