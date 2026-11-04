import { ClerkFailed, ClerkLoaded, ClerkLoading, ClerkProvider, SignIn, useAuth, useClerk } from '@clerk/react'
import { useLayoutEffect } from 'react'
import App from './App.tsx'
import { setSessionToken } from './session.ts'

// Clerk mode (#32): Clerk's React package around the app, loaded only when /api/health says clerk (main.tsx), so local
// mode fetches nothing of Clerk's. Signed out: the invitation page with Clerk's sign-in (the instance is invitation
// only, so an account without an invitation never gets past it). Signed in: the app, each API call with a fresh token.

export default function ClerkApp({ publishableKey }: { publishableKey: string }) {
  return (
    <ClerkProvider publishableKey={publishableKey}>
      <Gate />
    </ClerkProvider>
  )
}

function Gate() {
  const { isSignedIn } = useAuth()
  return isSignedIn ? <SignedInApp /> : <Invitation />
}

function SignedInApp() {
  const { getToken } = useAuth()
  const { signOut } = useClerk()
  // A layout effect runs before the app's own effects make their first API calls.
  useLayoutEffect(() => {
    setSessionToken(() => getToken())
    return () => setSessionToken(null)
  }, [getToken])
  return <App onSignOut={() => void signOut()} />
}

/** The page every visitor sees until signed in. */
function Invitation() {
  return (
    <>
      <header className="top">
        <div className="top-in wrap">
          <div className="brand"><b>Vouch</b><span>Audit preparation</span></div>
        </div>
      </header>
      <main className="dossier wrap">
        <section className="hero">
          <span className="pill">By invitation</span>
          <h1>Access is by invitation</h1>
          <p>
            Vouch is available to invited reviewers only. If you have received an invitation, sign in with the
            e-mail address it was sent to.
          </p>
        </section>
        <section className="signin" aria-label="Sign in">
          <ClerkLoading><p className="signin-note">Opening Vouch…</p></ClerkLoading>
          <ClerkFailed>
            <p className="signin-note" role="alert">Sign-in could not be loaded. Please try again shortly.</p>
          </ClerkFailed>
          <ClerkLoaded>
            {/* Hash routing: the app's own paths stay the app's. */}
            <SignIn routing="hash" />
          </ClerkLoaded>
        </section>
      </main>
    </>
  )
}
