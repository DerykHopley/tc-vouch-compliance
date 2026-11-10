import { Fragment, type ReactNode } from 'react'
import { proseBlocks, type TextRun } from './prose.ts'

/** How a run's text is set: as it is, unless the caller sets it otherwise (the report's long tokens, #530). */
type SetText = (text: string) => ReactNode
const asItIs: SetText = (text) => text

function Runs({ runs, set }: { runs: TextRun[]; set: SetText }) {
  return runs.map((run, i) => run.bold ? <strong key={i}>{set(run.text)}</strong>
    : run.italic ? <em key={i}>{set(run.text)}</em> : <Fragment key={i}>{set(run.text)}</Fragment>)
}

/** The model's text with its Markdown shown (#209): bold, italics, lists, headings (#232) and paragraphs, as elements. Everything else
 * is text, never HTML. The one renderer for chat replies and the draft answer. `set` sets each run's text (#530). */
export default function Prose({ text, set = asItIs }: { text: string; set?: SetText }) {
  return proseBlocks(text).map((block, i) => {
    if (block.kind === 'h') return <h3 key={i} className="prose-h"><Runs runs={block.runs} set={set} /></h3>
    if (block.kind === 'p') {
      return (
        <p key={i} className="prose-p">
          {block.lines.map((line, j) => <Fragment key={j}>{j > 0 && <br />}<Runs runs={line} set={set} /></Fragment>)}
        </p>
      )
    }
    const List = block.kind // ul or ol
    const subs = block.kind === 'ol' ? block.subs : undefined // a numbered item's sub-bullets, nested in it (#223)
    return (
      <List key={i} className="prose-list">
        {block.items.map((item, j) => (
          <li key={j}>
            <Runs runs={item} set={set} />
            {subs?.[j].length
              ? <ul className="prose-list">{subs[j].map((sub, k) => <li key={k}><Runs runs={sub} set={set} /></li>)}</ul>
              : null}
          </li>
        ))}
      </List>
    )
  })
}
