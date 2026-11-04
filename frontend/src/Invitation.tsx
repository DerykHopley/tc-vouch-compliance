import type { ReactNode } from 'react'
import TopBar from './TopBar.tsx'

/** The page every visitor sees until signed in (clerk mode); `children` is the sign-in, or why it can't show. */
export default function Invitation({ children }: { children: ReactNode }) {
  return (
    <>
      <TopBar />
      <main className="dossier wrap">
        <section className="hero">
          <span className="pill">By invitation</span>
          <h1>Access is by invitation</h1>
          <p>
            Vouch is available to invited users only. If you have received an invitation, sign in with the e-mail
            address it was sent to.
          </p>
        </section>
        <section className="signin" aria-label="Sign in">{children}</section>
      </main>
    </>
  )
}

/** Shown when Clerk's chunk, or the signed-in app, fails to render: reloading fetches it again. */
export function LoadFailed() {
  return <p className="signin-note" role="alert">Vouch could not be loaded. Please reload the page.</p>
}

/** Shown in place of Clerk's form when it can't load. */
export function SignInFailed() {
  return <p className="signin-note" role="alert">Sign-in could not be loaded. Please try again shortly.</p>
}
