import { Fragment } from 'react'
import { proseBlocks, type Run } from './prose.ts'

function Runs({ runs }: { runs: Run[] }) {
  return runs.map((run, i) => run.bold ? <strong key={i}>{run.text}</strong>
    : run.italic ? <em key={i}>{run.text}</em> : <Fragment key={i}>{run.text}</Fragment>)
}

/** The model's text with its Markdown shown (#209): bold, italics, lists and paragraphs, as elements. Everything else
 * is text, never HTML. The one renderer for chat replies and the draft answer. */
export default function Prose({ text }: { text: string }) {
  return proseBlocks(text).map((block, i) => block.kind === 'p'
    ? <p key={i} className="prose-p">
        {block.lines.map((line, j) => <Fragment key={j}>{j > 0 && <br />}<Runs runs={line} /></Fragment>)}
      </p>
    : block.kind === 'ul'
      ? <ul key={i} className="prose-list">{block.items.map((item, j) => <li key={j}><Runs runs={item} /></li>)}</ul>
      : <ol key={i} className="prose-list">{block.items.map((item, j) => <li key={j}><Runs runs={item} /></li>)}</ol>)
}
