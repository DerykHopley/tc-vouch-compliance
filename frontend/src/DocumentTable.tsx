import type { ReactNode } from 'react'
import type { TableWords } from './documents.ts'
import type { ChosenEvidence, LibraryDocument, LibrarySource } from './setup.ts'

/** A document as the table shows it: the sample library's, or a profile's from any source (patch too). */
export type ShownDocument = Omit<LibraryDocument, 'source'> & { source: string }

type Props<D extends ShownDocument> = {
  caption: string
  documents: readonly D[]
  /** The chosen documents, by folder; with `onToggle`, each row leads with a checkbox (the sample library's only). */
  selected?: ChosenEvidence
  onToggle?: (id: string, source: LibrarySource) => void
  /** The headings and cell words, in the page's language (#424); without them, the table's own English, the stale
   * reason shown. With them, the status shows the stale word alone: the approval date and cycle beside it say why. */
  words?: TableWords
  /** A last column, given each document (the Documents tab's Cited by). */
  extraColumn?: { heading: string; cell: (document: D) => ReactNode }
}

const ENGLISH: TableWords = {
  columns: { document: 'Document', kind: 'Kind', version: 'Version', approved: 'Approved', review: 'Review',
    status: 'Status', cited_by: 'Cited by' },
  kinds: { documented: 'documented', implemented: 'implemented' },
  cycles: {}, // shown as written
  stale: 'Stale', current: 'Current', no_version: 'no version', no_date: 'no date', no_cycle: 'none',
}

/**
 * Evidence documents with what Vouch has read from each (#408): title and id, kind, version, approval date, review
 * cycle, and stale or current, as the judge reads them. Company setup's step 6 chooses with its checkboxes; the
 * Documents tab (#424) shows it read-only, in the profile's words, with a Cited by column. Each title carries its
 * document's language. Wide, it scrolls inside its own container (design.md, Layout).
 */
export default function DocumentTable<D extends ShownDocument>({ caption, documents, selected, onToggle, words,
  extraColumn }: Props<D>) {
  const w = words ?? ENGLISH
  const staleWord = (d: D) => words || !d.stale_because ? w.stale : `Stale: ${d.stale_because}`
  return (
    <div className="table-wrap doc-table-wrap">
      <table className="report-table doc-table">
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
            {extraColumn && <th>{extraColumn.heading}</th>}
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
                      checked={selected?.[d.source as LibrarySource].includes(d.id) ?? false}
                      onChange={() => onToggle(d.id, d.source as LibrarySource)} />
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
              <td>{d.review_cycle === null ? w.no_cycle : w.cycles[d.review_cycle] ?? d.review_cycle}</td>
              <td>
                {d.stale
                  ? <span className="flag">{staleWord(d)}</span>
                  : <span className="doc-current">{w.current}</span>}
              </td>
              {extraColumn && <td>{extraColumn.cell(d)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
