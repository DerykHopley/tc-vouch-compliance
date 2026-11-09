import type { ChosenEvidence, LibraryDocument, LibrarySource } from './setup.ts'

type Props = {
  caption: string
  documents: readonly LibraryDocument[]
  /** The chosen documents, by folder; with `onToggle`, each row leads with a checkbox. */
  selected?: ChosenEvidence
  onToggle?: (id: string, source: LibrarySource) => void
  /** The `data-problem` the first checkbox carries, so Next can focus it (company setup's step 6). */
  firstProblem?: string
}

/**
 * Evidence documents with what Vouch has read from each (#408): title and id, kind, version, approval date, review
 * cycle, and stale or current, as the judge reads them. Company setup's step 6 chooses with its checkboxes; slice 4b's
 * Documents tab is to show it read-only, with a column of its own. Wide, it scrolls inside its own container
 * (design.md, Layout).
 */
export default function DocumentTable({ caption, documents, selected, onToggle, firstProblem }: Props) {
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
          {documents.map((d, i) => (
            <tr key={`${d.source}/${d.id}`}>
              {onToggle && (
                <td className="doc-choose">
                  <span className="setup-choice setup-check">
                    <input type="checkbox" aria-label={`Choose ${d.title}`}
                      checked={selected?.[d.source].includes(d.id) ?? false}
                      data-problem={i === 0 ? firstProblem : undefined}
                      onChange={() => onToggle(d.id, d.source)} />
                  </span>
                </td>
              )}
              <th scope="row">
                <span className="doc-title">{d.title}</span>
                <span className="mono doc-id">{d.id}</span>
              </th>
              <td><span className={`kind kind-${d.kind}`}>{d.kind}</span></td>
              <td>{d.version}</td>
              <td>{d.approval_date ?? 'no date'}</td>
              <td>{d.review_cycle ?? 'none'}</td>
              <td>
                {d.stale
                  ? <><span className="flag" title={d.stale_because ?? undefined}>Stale</span>
                    <span className="sr-only">: {d.stale_because}</span></>
                  : <span className="doc-current">Current</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
