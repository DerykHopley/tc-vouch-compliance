import type { ReactNode } from 'react'

/** A quote and where it is from (design.md, Components: Citation): the quote in serif italic with the brass rule, then
 * its source. The working paper's source is the whole citation; the chat's, the document id only. */
export default function CitedQuote({ quote, children }: { quote: string; children: ReactNode }) {
  return (
    <figure className="cite">
      <blockquote>{quote}</blockquote>
      <figcaption>{children}</figcaption>
    </figure>
  )
}
