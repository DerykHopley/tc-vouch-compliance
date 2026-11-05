import { ClerkFailed, ClerkLoaded, ClerkLoading, ClerkProvider, SignIn, useAuth, useClerk } from '@clerk/react'
import { useEffect, useLayoutEffect, useState } from 'react'
import { type Access, checkAccess } from './access.ts'
import App from './App.tsx'
import type { Gate } from './authConfig.ts'
import Invitation, { Page, SignInFailed } from './Invitation.tsx'
import { setOnSignedOut, setTokenSource } from './session.ts'

// Clerk mode (#32): Clerk's React package around the app, loaded only when /api/health says clerk (Root.tsx), so local
// mode fetches nothing of Clerk's. Signed out: the invitation page with Clerk's sign-in (the instance is invitation
// only, so an account without an invitation never gets past it). Signed in: the app, each API call with a fresh token;
// an API 401 signs out, back to the invitation page. With the course's Discord server as the gate (#152), Clerk lets
// anyone sign in with Discord, and the app shows only once the API says this user may use Vouch.

/** Clerk's form in Vouch's tokens (docs/design.md), read from the page's CSS, so it follows light and dark. */
const APPEARANCE = {
  variables: {
    colorPrimary: 'var(--ink)',
    colorPrimaryForeground: 'var(--sheet)',
    colorBackground: 'var(--sheet)',
    colorForeground: 'var(--ink)',
    colorMuted: 'var(--paper)',
    colorMutedForeground: 'var(--muted)',
    colorInput: 'var(--sheet)',
    colorInputForeground: 'var(--ink)',
    colorBorder: 'var(--rule)',
    colorNeutral: 'var(--ink)',
    colorRing: 'var(--navy)',
    colorShadow: 'var(--shadow)',
    fontFamily: 'var(--f-body)',
    borderRadius: '4px', // docs/design.md, Shape: panels 2 to 4px
  },
}

export default function ClerkApp({ publishableKey, gate = 'clerk' }: { publishableKey: string; gate?: Gate }) {
  return (
    <ClerkProvider publishableKey={publishableKey}>
      <SignedInOrOut gate={gate} />
    </ClerkProvider>
  )
}

function SignedInOrOut({ gate }: { gate: Gate }) {
  const { isSignedIn } = useAuth()
  return isSignedIn ? <SignedInApp /> : <SignedOut gate={gate} />
}

function SignedInApp() {
  const { getToken } = useAuth()
  const { signOut } = useClerk()
  // A layout effect runs before the app's own effects make their first API calls.
  useLayoutEffect(() => {
    setTokenSource(() => getToken())
    setOnSignedOut(() => void signOut())
    return () => {
      setTokenSource(null)
      setOnSignedOut(null)
    }
  }, [getToken, signOut])
  // Asked once on sign-in, and again on each "Try again" (`attempt`); an answer for a page that has gone is dropped.
  const [access, setAccess] = useState<Access | 'checking'>('checking')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let current = true
    void checkAccess().then((answer) => current && setAccess(answer))
    return () => { current = false }
  }, [attempt])
  const check = () => {
    setAccess('checking')
    setAttempt((n) => n + 1)
  }
  const leave = () => void signOut()
  if (access === 'allowed') return <App onSignOut={leave} />
  if (access === 'not-a-member') {
    return (
      <Page pill="Course members" title="Open to course members"
        text="Vouch is open to members of the course's Discord server, and the Discord account you signed in with is not in it. Sign out, then sign in with the Discord account you use there.">
        <p><button className="btn btn-primary" onClick={leave}>Sign out</button></p>
      </Page>
    )
  }
  if (access === 'sign-in-again') {
    return (
      <Page pill="Course members" title="Please sign in again"
        text="Vouch needs to see which Discord servers you are in, and your sign-in does not allow it yet. Sign out, then sign in with Discord again.">
        <p><button className="btn btn-primary" onClick={leave}>Sign out and sign in again</button></p>
      </Page>
    )
  }
  if (access === 'unavailable') {
    return (
      <Page pill="Course members" title="Opening Vouch" text="Vouch checks that your account may use it before it opens.">
        <section className="signin" aria-label="Sign in">
          <p className="signin-note" role="alert">Vouch cannot check your sign-in just now. Please try again shortly.</p>
          <p>
            <button className="btn btn-primary" onClick={check}>Try again</button>{' '}
            <button className="btn" onClick={leave}>Sign out</button>
          </p>
        </section>
      </Page>
    )
  }
  return <Page pill="Course members" title="Opening Vouch" text="Vouch checks that your account may use it before it opens."><p className="signin-note">Opening Vouch…</p></Page>
}

function SignedOut({ gate }: { gate: Gate }) {
  return (
    <Invitation gate={gate}>
      <ClerkLoading><p className="signin-note">Opening Vouch…</p></ClerkLoading>
      <ClerkFailed><SignInFailed /></ClerkFailed>
      <ClerkLoaded>
        {/* Hash routing: the app's own paths stay the app's. */}
        <SignIn routing="hash" appearance={APPEARANCE} />
      </ClerkLoaded>
    </Invitation>
  )
}
