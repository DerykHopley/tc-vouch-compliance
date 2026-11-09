import type { ChosenEvidence, LibraryDocument, LibrarySource } from './setup.ts'

type Props = {
  caption: string
  documents: readonly LibraryDocument[]
  /** The chosen documents, by folder; with `onToggle`, each row leads with a checkbox. */
  selected?: ChosenEvidence
  onToggle?: (id: string, source: LibrarySource) => void
}

/**
 * Evidence documents with what Vouch has read from each (#408): title and id, kind, version, approval date, review
 * cycle, and stale or current, as the judge reads them. Company setup's step 6 chooses with its checkboxes; slice 4b's
 * Documents tab is to show it read-only, with a column of its own. Wide, it scrolls inside its own container
 * (design.md, Layout).
 */
export default function DocumentTable({ caption, documents, selected, onToggle }: Props) {
  return (
    <div className="table-wrap doc-table-wrap">
      <table className="report-table doc-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {onToggle && <th><span className="sr-only">Chosen</span></th>}
            <th>Document</th>
            <th>Kind</th>
            <th>Version</th>
            <th>Approved</th>
            <th>Review</th>
            <th>Status</th>
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
                      checked={selected?.[d.source].includes(d.id) ?? false}
                      onChange={() => onToggle(d.id, d.source)} />
                  </label>
                </td>
              )}
              <th scope="row">
                <span className="doc-title">{d.title}</span>
                <span className="mono doc-id">{d.id}</span>
              </th>
              <td><span className={`kind kind-${d.kind}`}>{d.kind}</span></td>
              <td>{d.version ?? 'no version'}</td>
              <td>{d.approval_date ?? 'no date'}</td>
              <td>{d.review_cycle ?? 'none'}</td>
              <td>
                {d.stale
                  ? <span className="flag">{d.stale_because ? `Stale: ${d.stale_because}` : 'Stale'}</span>
                  : <span className="doc-current">Current</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
