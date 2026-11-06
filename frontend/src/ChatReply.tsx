import { replyParts } from './chat.ts'
import CitedQuote from './CitedQuote.tsx'
import Prose from './Prose.tsx'

/** Vouch's reply in the chat: its words with their Markdown shown (Prose, #209), each citation as a quote with its
 * document id. Never rendered as HTML. Marked with the profile's answer language, the one it answers in (#244). */
export default function ChatReply({ text, language }: { text: string; language?: string }) {
  return (
    <div className="msg" lang={language}>
      {replyParts(text).map((part, i) => 'quote' in part
        ? <CitedQuote key={i} quote={part.quote}><span className="mono">{part.doc_id}</span></CitedQuote>
        : <Prose key={i} text={part.text} />)}
    </div>
  )
}
