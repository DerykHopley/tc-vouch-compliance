import { useId, useRef, useState } from 'react'
import { dateWords } from './overview.ts'
import {
  ANSWER_LENGTH, answerQuestion, ASKED_OF_LENGTH, logQuestion, type OpenQuestion, QUESTION_LENGTH,
} from './questions.ts'

type Props = {
  controlId: string
  /** The control's questions from the overview, oldest first. */
  questions: OpenQuestion[]
  /** A question was logged or answered: the overview is read again, so the list and the card's flags follow. */
  onChanged: () => void
}

const SAY_WHAT = 'Say what is asked first.'
const SAY_WHO = 'Say who the question is for first.'
const CHOOSE_DUE = 'Choose a due date first.'
const WRITE_ANSWER = 'Write the answer first.'

/** A due date (YYYY-MM-DD) in words: "30 November 2026", read at local noon so no time zone moves its day. */
const dueWords = (due: string) => dateWords(`${due}T12:00:00`)

/** The questions as shown: the overview's, with any this paper kept since (logged, or answered) in place, oldest first.
 * The page's own copy wins until the overview carries the answer too. */
function merged(listed: OpenQuestion[], kept: OpenQuestion[]): OpenQuestion[] {
  const byId = new Map(listed.map((q) => [q.id, q]))
  for (const q of kept) {
    const there = byId.get(q.id)
    if (!there || (there.answer === null && q.answer !== null)) byId.set(q.id, q)
  }
  return [...byId.values()].sort((a, b) => a.asked_at.localeCompare(b.asked_at) || a.id - b.id)
}

/**
 * The working paper's open questions (#414; `docs/design.md`, Components: Open question), in both goals: each question
 * as the lead wrote it, who it is for and when it is due, an Overdue tag while it is overdue and unanswered, and its
 * answer once recorded (final). **Log a question** opens the form for a new one; **Record answer** the answer's. A blank
 * field is refused in the browser, naming it; a refusal shows the server's sentence. One request at a time.
 */
export default function OpenQuestions({ controlId, questions, onChanged }: Props) {
  const [kept, setKept] = useState<OpenQuestion[]>([])
  const [logging, setLogging] = useState(false)
  const shown = merged(questions, kept)
  const keep = (question: OpenQuestion) => {
    setKept((now) => [...now.filter((q) => q.id !== question.id), question])
    onChanged()
  }
  return (
    <section className="sheet questions" aria-label="Open questions">
      <span className="lbl">Open questions</span>
      {shown.length === 0 ? <p className="why">No open questions on this control.</p> : (
        <ul className="question-list">
          {shown.map((q) => <Question key={q.id} controlId={controlId} question={q} onAnswered={keep} />)}
        </ul>
      )}
      {logging ? <LogForm controlId={controlId} onClose={() => setLogging(false)}
        onLogged={(question) => { setLogging(false); keep(question) }} />
        : (
          <div className="review-actions">
            <button className="btn btn-ghost" type="button" onClick={() => setLogging(true)}>Log a question</button>
          </div>
        )}
    </section>
  )
}

function Question({ controlId, question, onAnswered }:
  { controlId: string; question: OpenQuestion; onAnswered: (question: OpenQuestion) => void }) {
  const [answering, setAnswering] = useState(false)
  return (
    <li className="question">
      <p className="question-text">{question.question}</p>
      <p className="why">
        For {question.asked_of} · Due {dueWords(question.due_date)}
        {question.overdue && <> <span className="flag">Overdue</span></>}
      </p>
      {question.answer !== null && question.answered_at !== null ? (
        <p className="why done-note">Answered on {dateWords(question.answered_at)}: {question.answer}</p>
      ) : answering ? (
        <AnswerForm controlId={controlId} question={question} onClose={() => setAnswering(false)}
          onAnswered={(answered) => { setAnswering(false); onAnswered(answered) }} />
      ) : (
        <div className="review-actions">
          <button className="btn btn-ghost" type="button" onClick={() => setAnswering(true)}>Record answer</button>
        </div>
      )}
    </li>
  )
}

/** The form for a new question: what, for whom, due when; all required. */
function LogForm({ controlId, onClose, onLogged }:
  { controlId: string; onClose: () => void; onLogged: (question: OpenQuestion) => void }) {
  const [text, setText] = useState('')
  const [askedOf, setAskedOf] = useState('')
  const [due, setDue] = useState('')
  const [sending, setSending] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)
  const busy = useRef(false) // one request at a time, whatever the clicks
  const id = useId()

  const send = async () => {
    if (busy.current) return
    const blank = text.trim() === '' ? SAY_WHAT : askedOf.trim() === '' ? SAY_WHO : due === '' ? CHOOSE_DUE : null
    if (blank) {
      setRefused(blank)
      return
    }
    busy.current = true
    setSending(true)
    setRefused(null)
    const answer = await logQuestion(controlId, { question: text.trim(), asked_of: askedOf.trim(), due_date: due })
    busy.current = false
    setSending(false)
    if (answer.ok) onLogged(answer.question)
    else setRefused(answer.message)
  }

  const why = refused ? `${id}-why` : undefined
  return (
    <form className="override" aria-label={`Log a question on ${controlId}`} noValidate aria-busy={sending}
      onSubmit={(e) => { e.preventDefault(); void send() }}>
      <label htmlFor={`${id}-question`}>Question</label>
      <textarea id={`${id}-question`} className="field" required value={text} maxLength={QUESTION_LENGTH}
        disabled={sending} aria-invalid={refused === SAY_WHAT} aria-describedby={refused === SAY_WHAT ? why : undefined}
        onChange={(e) => setText(e.target.value)} />
      <label htmlFor={`${id}-for`}>For whom</label>
      <input id={`${id}-for`} className="field" required value={askedOf} maxLength={ASKED_OF_LENGTH}
        placeholder="A role, such as IT lead" disabled={sending} aria-invalid={refused === SAY_WHO}
        aria-describedby={refused === SAY_WHO ? why : undefined} onChange={(e) => setAskedOf(e.target.value)} />
      <label htmlFor={`${id}-due`}>Due date</label>
      <input id={`${id}-due`} className="field" type="date" required value={due} disabled={sending}
        aria-invalid={refused === CHOOSE_DUE} aria-describedby={refused === CHOOSE_DUE ? why : undefined}
        onChange={(e) => setDue(e.target.value)} />
      <div className="review-actions">
        <button className="btn btn-primary" type="submit" disabled={sending} aria-busy={sending}>Log question</button>
        <button className="btn" type="button" disabled={sending} onClick={onClose}>Cancel</button>
      </div>
      {refused && <p id={why} className="field-note notice" role="alert">{refused}</p>}
    </form>
  )
}

/** The form for a question's answer: required, final once saved. */
function AnswerForm({ controlId, question, onClose, onAnswered }:
  { controlId: string; question: OpenQuestion; onClose: () => void; onAnswered: (question: OpenQuestion) => void }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)
  const busy = useRef(false)
  const id = useId()

  const send = async () => {
    if (busy.current) return
    if (text.trim() === '') {
      setRefused(WRITE_ANSWER)
      return
    }
    busy.current = true
    setSending(true)
    setRefused(null)
    const answer = await answerQuestion(controlId, question.id, text.trim())
    busy.current = false
    setSending(false)
    if (answer.ok) onAnswered(answer.question)
    else setRefused(answer.message)
  }

  return (
    <form className="override" aria-label="Record the answer" noValidate aria-busy={sending}
      onSubmit={(e) => { e.preventDefault(); void send() }}>
      <label htmlFor={`${id}-answer`}>Answer</label>
      <textarea id={`${id}-answer`} className="field" required value={text} maxLength={ANSWER_LENGTH} disabled={sending}
        aria-invalid={refused === WRITE_ANSWER} aria-describedby={refused ? `${id}-why` : undefined}
        onChange={(e) => setText(e.target.value)} />
      <div className="review-actions">
        <button className="btn btn-primary" type="submit" disabled={sending} aria-busy={sending}>Save answer</button>
        <button className="btn" type="button" disabled={sending} onClick={onClose}>Cancel</button>
      </div>
      {refused && <p id={`${id}-why`} className="field-note notice" role="alert">{refused}</p>}
    </form>
  )
}
