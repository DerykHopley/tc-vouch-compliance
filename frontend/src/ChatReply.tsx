import { replyParts } from './chat.ts'
import CitedQuote from './CitedQuote.tsx'

/** Vouch's reply in the chat: its words as text, each citation as a quote with its document id. Never rendered as
 * HTML. */
export default function ChatReply({ text }: { text: string }) {
  return (
    <div className="msg">
      {replyParts(text).map((part, i) => 'quote' in part
        ? <CitedQuote key={i} quote={part.quote}><span className="mono">{part.doc_id}</span></CitedQuote>
        : part.text)}
    </div>
  )
}
