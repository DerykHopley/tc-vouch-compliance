import { replyParts } from './chat.ts'

/** Vouch's reply in the chat: its words as text, each cite as a quote in the working paper's Citation style (design.md,
 * Components: serif italic with the brass rule) with its document id. Never rendered as HTML. */
export default function ChatReply({ text }: { text: string }) {
  return (
    <div className="msg">
      {replyParts(text).map((part, i) => 'quote' in part
        ? (
          <figure className="cite" key={i}>
            <blockquote>{part.quote}</blockquote>
            <figcaption><span className="mono">{part.doc}</span></figcaption>
          </figure>
        )
        : part.text)}
    </div>
  )
}
