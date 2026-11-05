import type { ReactNode } from 'react'
import type { Gate } from './authConfig.ts'
import TopBar from './TopBar.tsx'
import { BY_INVITATION, COURSE_MEMBERS } from './words.ts'

/**
 * The page every visitor sees until signed in (clerk mode); `children` is the sign-in, or why it can't show. Its words
 * follow who may sign in: the course's Discord server's members (#152), or invited users.
 */
export default function Invitation({ gate = 'clerk', children }: { gate?: Gate; children: ReactNode }) {
  return (
    <Page {...(gate === 'discord-server' ? COURSE_MEMBERS : BY_INVITATION)}>
      <section className="signin" aria-label="Sign in">{children}</section>
    </Page>
  )
}

/** The top bar and a Dossier-style hero, then `children`: the shape of every page before the app. */
export function Page({ pill, title, text, children }: { pill: string; title: string; text: string; children?: ReactNode }) {
  return (
    <>
      <TopBar />
      <main className="dossier wrap">
        <section className="hero">
          <span className="pill">{pill}</span>
          <h1>{title}</h1>
          <p>{text}</p>
        </section>
        {children}
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
