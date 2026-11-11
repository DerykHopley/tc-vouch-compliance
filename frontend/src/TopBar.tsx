import { type ReactNode, useRef } from 'react'
import { goalWording } from './overview.ts'
import { profileTag, type ProfileSummary } from './profiles.ts'
import type { Goal } from './report.ts'
import { useHeightVar } from './useHeightVar.ts'

/** The company the top bar names: the overview's company once it is read, the listed profile's summary before. */
export type BarCompany = Pick<ProfileSummary, 'company' | 'country' | 'answer_language'>

/** The top bar on every page: Vouch's name and what it is for in the profile's goal (#440: `goal` and `language`,
 * the profile's answer language, once the profile is read), then a rule and the current company's name with its tag
 * (#495, option A "Letterhead": who Vouch works for, plain text that switches nothing; none without `company`), then
 * whatever the page puts beside it. Its height, which grows at phone width, is kept in `--top-bar` on the page
 * (app.css: scroll-padding-top), so a field brought into view by focus or by scrolling to it stops below the sticky
 * bar, not under it (#205). */
export default function TopBar({ children, goal, language, company }:
  { children?: ReactNode; goal?: Goal; language?: string; company?: BarCompany }) {
  const bar = useRef<HTMLElement>(null)
  useHeightVar(bar, '--top-bar')
  const { label, lang } = goalWording(goal, language)
  return (
    <header ref={bar} className="top">
      <div className="top-in wrap">
        <div className="top-id">
          <div className="brand"><b>Vouch</b><span lang={lang}>{label}</span></div>
          {company && (
            <p className="top-company">
              <span className="top-rule" aria-hidden="true" />
              <span className="top-who">
                <span className="top-name">{company.company}</span>
                <span className="top-tag">{profileTag(company)}</span>
              </span>
            </p>
          )}
        </div>
        {children}
      </div>
    </header>
  )
}
