import { type ReactNode, useEffect, useRef, useState } from 'react'
import { type TableWords, wordFor } from './documents.ts'
import type { LibraryDocument } from './setup.ts'

/** A document as the table shows it: the sample library's, or a profile's from any source (patch too). */
export type ShownDocument = Omit<LibraryDocument, 'source'> & { source: string; withdrawn?: boolean }

type Props<D extends ShownDocument> = {
  caption: string
  documents: readonly D[]
  /** The chosen documents, by folder; with `onToggle`, each row leads with a checkbox (the sample library's only). */
  selected?: Partial<Record<D['source'], string[]>>
  onToggle?: (id: string, source: D['source']) => void
  /** The headings and cell words, in the page's language (#424); without them, the table's own English, the stale
   * reason shown. With them, an overdue document shows the stale word alone (the approval date and cycle beside it
   * say why), and one whose review date can't be worked out the stale word and a reason word. */
  words?: TableWords
  /** Last columns, each given each document (the Documents tab's Cited by and Origin). */
  extraColumns?: readonly { heading: string; cell: (document: D) => ReactNode }[]
}

// The table's own English, for company setup's step 6: the wizard keeps its lower-case kind words and shows cycles
// and the stale reason as written, so it isn't vouch.documents_tab.DOCUMENT_WORDS["en"], which the Documents tab is
// served. The stale reasons are unused here: without words, the server's reason is shown whole.
const ENGLISH: TableWords = {
  columns: { document: 'Document', kind: 'Kind', version: 'Version', approved: 'Approved', review: 'Review',
    status: 'Status', cited_by: 'Cited by', origin: 'Origin' },
  kinds: { documented: 'documented', implemented: 'implemented' },
  cycles: {}, // shown as written
  stale: 'Stale',
  stale_reasons: { no_approval_date: 'no approval date', no_review_cycle: 'no review cycle',
    unknown_cycle: 'unknown review cycle' },
  current: 'Current', no_version: 'no version', no_date: 'no date', no_cycle: 'none', withdrawn: 'Withdrawn',
}

/** Whether `selected` holds this document of this source. */
function isChosen<S extends string>(selected: Partial<Record<S, string[]>> | undefined, source: S, id: string) {
  return selected?.[source]?.includes(id) ?? false
}

/** With words, why a stale document's review date can't be worked out, or null for an overdue one (its approval
 * date and known cycle say why). */
function staleReason(d: ShownDocument, words: TableWords): string | null {
  if (d.approval_date === null) return words.stale_reasons.no_approval_date
  if (d.review_cycle === null) return words.stale_reasons.no_review_cycle
  if (wordFor(words.cycles, d.review_cycle) === undefined) return words.stale_reasons.unknown_cycle
  return null
}

/**
 * Evidence documents with what Vouch has read from each (#408): title and id, kind, version, approval date, review
 * cycle, and stale or current, as the judge reads them. Company setup's step 6 chooses with its checkboxes; the
 * Documents tab (#424) shows it in the profile's words, with Cited by and Origin columns (#585). Each title carries its
 * document's language. Wide, it scrolls inside its own container (design.md, Layout), and shows that it does: while
 * columns lie beyond its right edge, `data-more` marks the box around it, which app.css fades (#607, #555's hint).
 */
export default function DocumentTable<D extends ShownDocument>({ caption, documents, selected, onToggle, words,
  extraColumns = [] }: Props<D>) {
  const wrap = useRef<HTMLDivElement>(null)
  const table = useRef<HTMLTableElement>(null)
  const [more, setMore] = useState(false)
  useEffect(() => {
    const box = wrap.current
    if (!box) return
    const measure = () => setMore(box.scrollWidth - box.scrollLeft - box.clientWidth > 1)
    measure()
    box.addEventListener('scroll', measure, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(box)
    if (table.current) observer?.observe(table.current)
    return () => {
      box.removeEventListener('scroll', measure)
      observer?.disconnect()
    }
  }, [])
  const w = words ?? ENGLISH
  const staleWord = (d: D) => {
    const reason = words ? staleReason(d, words) : d.stale_because
    return reason ? `${w.stale}: ${reason}` : w.stale
  }
  return (
    <div className="doc-table-hint" data-more={more || undefined}>
      <div className="table-wrap doc-table-wrap" ref={wrap}>
        <table className="report-table doc-table" ref={table}>
          <caption>{caption}</caption>
          <thead>
            <tr>
              {onToggle && <th><span className="sr-only">Chosen</span></th>}
              <th>{w.columns.document}</th>
              <th>{w.columns.kind}</th>
              <th>{w.columns.version}</th>
              <th>{w.columns.approved}</th>
              <th>{w.columns.review}</th>
              <th>{w.columns.status}</th>
              {extraColumns.map((c) => <th key={c.heading}>{c.heading}</th>)}
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr key={`${d.source}/${d.id}`}>
                {onToggle && (
                  <td className="doc-choose">
                    {/* A label, so the whole 44px cell takes the press, in forced colours too (the box is drawn 18px). */}
                    <label className="setup-choice setup-check">
                      <input type="checkbox" aria-label={`Choose ${d.title}`}
                        checked={isChosen(selected, d.source, d.id)}
                        onChange={() => onToggle(d.id, d.source)} />
                    </label>
                  </td>
                )}
                <th scope="row">
                  <span className="doc-title" lang={d.language ?? undefined}>{d.title}</span>
                  <span className="mono doc-id">{d.id}</span>
                </th>
                <td><span className={`kind kind-${d.kind}`}>{w.kinds[d.kind]}</span></td>
                <td>{d.version ?? w.no_version}</td>
                <td>{d.approval_date ?? w.no_date}</td>
                <td>{d.review_cycle === null ? w.no_cycle : wordFor(w.cycles, d.review_cycle) ?? d.review_cycle}</td>
                <td>
                  {d.withdrawn ? <span className="doc-withdrawn">{w.withdrawn}</span>
                    : d.stale ? <span className="flag">{staleWord(d)}</span>
                      : <span className="doc-current">{w.current}</span>}
                </td>
                {extraColumns.map((c) => <td key={c.heading}>{c.cell(d)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
