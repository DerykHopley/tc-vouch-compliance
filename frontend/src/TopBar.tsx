import type { ReactNode } from 'react'

/** The top bar on every page: Vouch's name, then whatever the page puts beside it. */
export default function TopBar({ children }: { children?: ReactNode }) {
  return (
    <header className="top">
      <div className="top-in wrap">
        <div className="brand"><b>Vouch</b><span>Audit preparation</span></div>
        {children}
      </div>
    </header>
  )
}
