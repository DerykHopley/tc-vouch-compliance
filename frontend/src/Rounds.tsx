import { dateWords } from './overview.ts'
import { type ListedRound, type RoundsState, roundPath } from './rounds.ts'

/** The Company page's Rounds section (#572; `docs/design.md`, Components: Rounds section): the lead's review rounds,
 * newest first, each closed one since the fresh start a link to its round comparison, the default choice marked. Its
 * states stay inside the panel, as the Documents panel's, so the rest of the page stays up. */
export default function Rounds({ state, tryAgain }: { state: RoundsState; tryAgain: () => void }) {
  if (state.status === 'loading') return <p className="lbl" role="status">Loading the review rounds…</p>
  if (state.status === 'failed') {
    return (
      <div className="notice">
        <p role="alert" className="answer">{state.message}</p>
        <button className="btn" onClick={tryAgain}>Try again</button>
      </div>
    )
  }
  const { rounds, default: chosen } = state.rounds
  return (
    <>
      <ol className="company-list rounds-list">
        {[...rounds].reverse().map((round) => <Round key={round.number} round={round} chosen={chosen} />)}
      </ol>
      {chosen === null && <p>Close a review round to compare with it later.</p>}
    </>
  )
}

function Round({ round, chosen }: { round: ListedRound; chosen: number | null }) {
  const opened = round.opened_at === null ? 'the start' : dateWords(round.opened_at)
  const times = round.closed_at === null ? `Open since ${opened}`
    : `${round.opened_at === null ? 'Opened at' : 'Opened'} ${opened}, closed ${dateWords(round.closed_at)}`
  const body = <><span className="rounds-name">{round.name}</span><span className="rounds-times">{times}</span></>
  return (
    <li>
      {round.closed_at !== null && !round.before_fresh_start
        ? <a className="rounds-link" href={roundPath(round.number)}>{body}</a>
        : <div className="rounds-row">{body}</div>}
      {round.number === chosen && <span className="pill rounds-tag">Compared by default</span>}
      {round.before_fresh_start && <span className="rounds-note">Closed before the fresh start</span>}
    </li>
  )
}
