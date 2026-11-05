import { ClerkFailed, ClerkLoaded, ClerkLoading, ClerkProvider, SignIn, useAuth, useClerk } from '@clerk/react'
import { useLayoutEffect } from 'react'
import Invitation, { SignInFailed } from './Invitation.tsx'
import ProfiledApp from './ProfiledApp.tsx'
import { setOnSignedOut, setTokenSource } from './session.ts'

// Clerk mode (#32): Clerk's React package around the app, loaded only when /api/health says clerk (Root.tsx), so local
// mode fetches nothing of Clerk's. Signed out: the invitation page with Clerk's sign-in (the instance is invitation
// only, so an account without an invitation never gets past it). Signed in: the app, each API call with a fresh token;
// an API 401 signs out, back to the invitation page.

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

export default function ClerkApp({ publishableKey }: { publishableKey: string }) {
  return (
    <ClerkProvider publishableKey={publishableKey}>
      <Gate />
    </ClerkProvider>
  )
}

function Gate() {
  const { isSignedIn } = useAuth()
  return isSignedIn ? <SignedInApp /> : <SignedOut />
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
  return <ProfiledApp onSignOut={() => void signOut()} />
}

function SignedOut() {
  return (
    <Invitation>
      <ClerkLoading><p className="signin-note">Opening Vouch…</p></ClerkLoading>
      <ClerkFailed><SignInFailed /></ClerkFailed>
      <ClerkLoaded>
        {/* Hash routing: the app's own paths stay the app's. */}
        <SignIn routing="hash" appearance={APPEARANCE} />
      </ClerkLoaded>
    </Invitation>
  )
}
