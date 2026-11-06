import { replyParts } from './chat.ts'
import CitedQuote from './CitedQuote.tsx'
import Prose from './Prose.tsx'

/** Vouch's reply in the chat: its words with their Markdown shown (Prose, #209), each citation as a quote with its
 * document id. Never rendered as HTML. */
export default function ChatReply({ text }: { text: string }) {
  return (
    <div className="msg">
      {replyParts(text).map((part, i) => 'quote' in part
        ? <CitedQuote key={i} quote={part.quote}><span className="mono">{part.doc_id}</span></CitedQuote>
        : <Prose key={i} text={part.text} />)}
    </div>
  )
}
