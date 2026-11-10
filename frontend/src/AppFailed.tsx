import { Component, Fragment, type ReactNode } from 'react'
import TopBar from './TopBar.tsx'

type Props = { children: ReactNode }
type State = { failed: boolean; attempt: number }

/**
 * The app's own error boundary (issue 455): a render error inside the app shows "Vouch could not show this page" with
 * Try again, which mounts the app afresh. Never the sign-in page's words: whoever sees this is past sign-in (clerk
 * mode) or needs none (local mode). Root's boundary stays for what fails before the app, Clerk's chunk.
 */
export default class AppFailed extends Component<Props, State> {
  state: State = { failed: false, attempt: 0 }

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true }
  }

  render() {
    // Keyed by the attempt: Try again mounts the app afresh, its state read again.
    if (!this.state.failed) return <Fragment key={this.state.attempt}>{this.props.children}</Fragment>
    const again = () => this.setState(({ attempt }) => ({ failed: false, attempt: attempt + 1 }))
    return (
      <>
        <TopBar />
        <main className="dossier wrap">
          <section className="hero">
            <p role="alert">Vouch could not show this page. Please try again.</p>
            <p><button className="btn btn-primary" onClick={again}>Try again</button></p>
          </section>
        </main>
      </>
    )
  }
}

