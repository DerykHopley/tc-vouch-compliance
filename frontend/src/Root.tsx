import { Component, lazy, type ReactNode, Suspense } from 'react'
import AppFailed from './AppFailed.tsx'
import type { AuthConfig } from './authConfig.ts'
import Invitation, { LoadFailed } from './Invitation.tsx'
import ProfiledApp from './ProfiledApp.tsx'
import TopBar from './TopBar.tsx'

// Clerk's package is its own chunk, fetched only in clerk mode (#32).
const ClerkApp = lazy(() => import('./ClerkApp.tsx'))

/** The app as the API's auth mode needs it: behind Clerk's sign-in, as it is locally, or why it can't open. */
export default function Root({ auth }: { auth: AuthConfig }) {
  if (auth.mode === 'local') return <AppFailed><ProfiledApp /></AppFailed>
  if (auth.mode === 'unreachable') return <Unreachable />
  return (
    <ChunkFailed fallback={<Invitation gate={auth.gate}><LoadFailed /></Invitation>}>
      <Suspense fallback={null}>
        <ClerkApp publishableKey={auth.publishableKey} gate={auth.gate} />
      </Suspense>
    </ChunkFailed>
  )
}

function Unreachable() {
  return (
    <>
      <TopBar />
      <main className="dossier wrap">
        <section className="hero">
          <p role="alert">Vouch could not be reached. Please try again shortly.</p>
        </section>
      </main>
    </>
  )
}

/** Shows `fallback`, not a blank page, when Clerk's chunk can't load (its import rejects); the app inside has its own
 * boundary (AppFailed), so a render error there never shows the sign-in page (issue 455). */
class ChunkFailed extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}
