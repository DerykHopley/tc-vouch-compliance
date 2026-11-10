import type { ReactNode } from 'react'

/** A quote and where it is from (design.md, Components: Citation): the quote in serif italic with the brass rule, then
 * its source. The working paper's source is the whole citation; the chat's, the document id only. `lang` is the quote's
 * language, which its long words hyphenate in (#511); without it the quote takes its surroundings' language. */
export default function CitedQuote({ quote, lang, children }: { quote: string; lang?: string; children: ReactNode }) {
  return (
    <figure className="cite">
      <blockquote lang={lang}>{quote}</blockquote>
      <figcaption>{children}</figcaption>
    </figure>
  )
}
