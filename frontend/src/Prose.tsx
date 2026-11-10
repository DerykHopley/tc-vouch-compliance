import { Fragment } from 'react'
import { proseBlocks, type TextRun } from './prose.ts'

function Runs({ runs }: { runs: TextRun[] }) {
  return runs.map((run, i) => run.bold ? <strong key={i}>{run.text}</strong>
    : run.italic ? <em key={i}>{run.text}</em> : <Fragment key={i}>{run.text}</Fragment>)
}

/** The model's text with its Markdown shown (#209): bold, italics, lists, headings (#232) and paragraphs, as elements. Everything else
 * is text, never HTML. The one renderer for chat replies and the draft answer. */
export default function Prose({ text }: { text: string }) {
  return proseBlocks(text).map((block, i) => {
    if (block.kind === 'h') return <h3 key={i} className="prose-h"><Runs runs={block.runs} /></h3>
    if (block.kind === 'p') {
      return (
        <p key={i} className="prose-p">
          {block.lines.map((line, j) => <Fragment key={j}>{j > 0 && <br />}<Runs runs={line} /></Fragment>)}
        </p>
      )
    }
    const List = block.kind // ul or ol
    const subs = block.kind === 'ol' ? block.subs : undefined // a numbered item's sub-bullets, nested in it (#223)
    return (
      <List key={i} className="prose-list">
        {block.items.map((item, j) => (
          <li key={j}>
            <Runs runs={item} />
            {subs?.[j].length ? <ul className="prose-list">{subs[j].map((sub, k) => <li key={k}><Runs runs={sub} /></li>)}</ul> : null}
          </li>
        ))}
      </List>
    )
  })
}
