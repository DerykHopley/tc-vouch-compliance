import { useCallback, useEffect, useRef, useState } from 'react'
import App, { DEVELOPER_PATH } from './App.tsx'
import type { Arrival } from './arrival.ts'
import { makeCurrent } from './developer.ts'
import { fetchProfiles, type Profiles, type ProfileSummary, withoutProfile, withProfile } from './profiles.ts'
import type { Goal } from './report.ts'
import { setProfile, signOutCount } from './session.ts'
import { leaveSetup } from './setupHistory.ts'
import TopBar from './TopBar.tsx'

/** The profile list as read: not yet, or what came back (null: it couldn't be read). */
type Listing = { status: 'reading' } | { status: 'read'; profiles: Profiles | null }

/**
 * The app under the current company (#480, M9 decision 1): one shared setting a developer chooses on the developer
 * page, so the browser chooses nothing. The app opens only once the list has answered, under its `current`, pinned for
 * this page's life (setProfile), so every call of one open page is under one company; another developer's Make current
 * reaches it on its next reload. Without a list, calls name none and the API's current company answers.
 *
 * The app opens afresh (nothing the page holds carries over) on a fresh start from the user menu (#285), on the
 * Overview; on a Make current, under the new current company, on the developer page; when the company shown was
 * removed (#418), under the current company left (Nordlicht, decision 8), on the developer page; and when company setup
 * made a company (#409), which is made current, then opened with its `arrival` (#447): focus on the Dossier's heading,
 * the Goal chosen in setup set and a profile pass run. The arrival is used as the app opens and cleared then, so a
 * fresh start, a removal or a reload opens the app without it. A company made but not made current opens the developer
 * page under the current company instead, saying so. A developer page opened afresh carries its note (`developerNote`).
 */
export default function ProfiledApp({ onSignOut, userName }: { onSignOut?: () => void; userName?: string }) {
  const [listing, setListing] = useState<Listing>({ status: 'reading' })
  const [chosen, setChosen] = useState<string | null>(null)
  const [opens, setOpens] = useState(0) // each count opens the app afresh, under the same company or another
  const [developerNote, setDeveloperNote] = useState<string | null>(null) // carried to the developer page, said once
  const [arrival, setArrival] = useState<Arrival | null>(null) // a company was just made, until the app opened uses it
  const arrivalUsed = useCallback(() => setArrival(null), [])
  const open = useRef(true) // false once closed (signed out): a late answer then changes nothing
  // The list held and the company shown, as last rendered: the one way a late answer reads them (#418).
  const latest = useRef<{ profiles: Profiles | null; chosen: string | null }>({ profiles: null, chosen: null })
  useEffect(() => {
    latest.current = { profiles: listing.status === 'read' ? listing.profiles : null, chosen }
  }, [listing, chosen])
  useEffect(() => {
    open.current = true
    return () => {
      open.current = false
    }
  }, [])
  // Opens the app afresh under `id` (none: the API's current company answers), with `note` for the developer page.
  const reopen = (id: string | null, note: string | null = null) => {
    setProfile(id) // before the app opens again, so its first calls name it
    setChosen(id)
    setDeveloperNote(note)
    setOpens((n) => n + 1)
  }
  // The lead started the company afresh (#285): the app opens again on the Overview.
  const startedAfresh = () => {
    toPath('/')
    reopen(latest.current.chosen)
  }
  // The profile list read again after a change, then `apply`, given the list read (or null, when it can't be) and the
  // list held and the company shown as they are when it answers. Closed or signed out meanwhile: nothing changes.
  const afterRelist = (apply: (found: Profiles | null, held: Profiles | null, shown: string | null) => void) => {
    const signOuts = signOutCount()
    void fetchProfiles().then((found) => {
      if (!open.current || signOutCount() !== signOuts) return
      apply(found, latest.current.profiles, latest.current.chosen)
    })
  }
  // A developer made a company current on the developer page (#480): the app opens afresh under it, there.
  const madeCurrent = (id: string, note: string) => reopen(id, note)
  // A developer made a company in company setup (#409): it is made current, then, once the list is read again, the app
  // opens afresh under it on the Dossier, focus on its heading, with the Goal chosen in setup for its first pass (#447).
  // Not made current: the developer page opens under the current company, saying so; the company is made all the same.
  // A list that can't be read again gains it, so its name is known still. Either way the setup's history entry gives
  // way (#476, leaveSetup); a developer who left setup while the create answered (Back, a tab) stays where they went,
  // and Back or Forward onto that entry later gives the page the create would have opened (#568).
  const created = (profile: ProfileSummary, goal: Goal, entry: string) => {
    const signOuts = signOutCount()
    void makeCurrent(profile.id).then((change) => {
      if (!open.current || signOutCount() !== signOuts || change.status === 'signed-out') return
      afterRelist((found, held, shown) => {
        setListing({ status: 'read', profiles: withProfile(found ?? held, profile) })
        if (change.status === 'made') {
          setArrival({ goal })
          leaveSetup(entry, '/')
          reopen(profile.id)
          return
        }
        leaveSetup(entry, DEVELOPER_PATH)
        reopen(found ? found.current : shown,
          `${profile.company} was made but could not be made current. Use Make current to try again.`)
      })
    })
  }
  // A developer removed a company made in setup (#418): the list is read again (or, when it can't be, the list held
  // less it). Removing another company changes only the list: the page stays as it is, its note naming the current
  // company when it changed meanwhile. Removing the one shown (it was current, decision 8) opens the app afresh, where
  // the developer is, under the current company the API names, carrying the developer page's note; with no list read
  // again, naming none, so the API's current company answers. Its fallback rule is the API's alone.
  const removed = (id: string, note: string) => afterRelist((found, held, shown) => {
    setListing({ status: 'read', profiles: found ?? withoutProfile(held, id) })
    if (shown === id) reopen(found ? found.current : null, note)
  })
  useEffect(() => {
    let current = true
    void fetchProfiles().then((found) => {
      if (!current) return
      const id = found?.current ?? null
      setProfile(id) // before the app opens, so its first calls name it
      setChosen(id)
      setListing({ status: 'read', profiles: found })
    })
    return () => {
      current = false
    }
  }, [])
  const forgetNote = useCallback(() => setDeveloperNote(null), [])
  useEffect(() => () => setProfile(null), []) // signing out leaves no company for the next sign-in

  if (listing.status === 'reading') return <TopBar />
  const profile = listing.profiles?.profiles.find((p) => p.id === chosen)
  return <App key={`${chosen ?? ''}:${opens}`} onSignOut={onSignOut} userName={userName} profile={profile}
    onStartedAfresh={startedAfresh} onCompanyCreated={created} onCompanyRemoved={removed} onMadeCurrent={madeCurrent}
    developerNote={developerNote ?? undefined} onNoteShown={forgetNote} arrival={arrival} onArrivalUsed={arrivalUsed} />
}

/** Puts `path` in the address bar, as a new history entry, unless it is there already. */
function toPath(path: string) {
  if (window.location.pathname !== path) window.history.pushState(null, '', path)
}
