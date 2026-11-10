import { useCallback, useEffect, useRef, useState } from 'react'
import App from './App.tsx'
import type { Arrival } from './arrival.ts'
import { mayLeave } from './leaving.ts'
import {
  fetchProfiles, type Profiles, type ProfileSummary, startingProfile, withoutProfile, withProfile,
} from './profiles.ts'
import ProfileSwitcher from './ProfileSwitcher.tsx'
import type { Goal } from './report.ts'
import { rememberedProfile, rememberProfile, setProfile, signOutCount } from './session.ts'
import TopBar from './TopBar.tsx'

/** The profile list as read: not yet, or what came back (null: it couldn't be read). */
type Listing = { status: 'reading' } | { status: 'read'; profiles: Profiles | null }

/**
 * The app under one compliance profile at a time (#47), with the profile switcher in its top bar. The app opens only
 * once the list has answered, under the default profile (or the first listed), so its first calls already name it.
 * Switching names the new profile on every API call and opens the app afresh, so nothing the page holds (overview,
 * working paper, run, chat) carries over from the profile before. Without a list, calls name none and the API's
 * default answers. The browser remembers the last switch, so a reload or a pasted link opens that profile while it is
 * still listed; otherwise the default, and the top bar says so (#201). A fresh start from the user menu (#285) opens
 * the app afresh the same way, on the Overview. A company made in company setup (#409) opens the same way, under it,
 * the list read again so the switcher names it. A company removed on the developer page (#418) leaves the switcher the
 * same way; when it was the one shown, the default opens in its place, on the developer page, and the top bar says so.
 * Only the app opened by a create gets an `arrival` (#447): focus on the Dossier's heading, the Goal chosen in setup set
 * and a profile pass run. The app uses it as it opens and it is cleared then, so a switch, a fresh start, a removal or
 * a reload opens the app without it.
 */
export default function ProfiledApp({ onSignOut, userName }: { onSignOut?: () => void; userName?: string }) {
  const [listing, setListing] = useState<Listing>({ status: 'reading' })
  const [chosen, setChosen] = useState<string | null>(null)
  const [fellBack, setFellBack] = useState(false)
  const [switched, setSwitched] = useState(false) // the user switched: the dropdown takes focus again
  const [freshStarts, setFreshStarts] = useState(0) // each fresh start opens the app afresh
  const [removedNote, setRemovedNote] = useState<string | null>(null) // the company shown was removed: said once
  const [arrival, setArrival] = useState<Arrival | null>(null) // a company was just made, until the app opened uses it
  const arrivalUsed = useCallback(() => setArrival(null), [])
  const open = useRef(true) // false once closed (signed out): a late answer then changes nothing
  // The list held and the profile shown, as last rendered: the one way a late answer reads them (#418).
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
  // Opens the app under `id`: at start (the remembered or the default profile) and on each switch.
  const choose = (id: string | null) => {
    setProfile(id) // before the app opens again, so its first calls name it
    setChosen(id)
  }
  // The user's switch in the menu: remembered for the next reload, and it ends any fallback note. A working paper
  // with overrides not yet recorded asks first (#182).
  const switchTo = (id: string) => {
    if (!mayLeave()) return
    rememberProfile(id)
    setFellBack(false)
    setSwitched(true)
    setRemovedNote(null)
    choose(id)
  }
  // The lead started the profile afresh (#285): the app opens again on the Overview, as after a switch.
  const startedAfresh = () => {
    toRoot()
    setRemovedNote(null)
    setFreshStarts((n) => n + 1)
  }
  // The profile list read again after a company was made or removed, then `apply`, given the list read (or null, when
  // it can't be) and the list held and the profile shown as they are when it answers. Closed or signed out meanwhile:
  // nothing changes.
  const afterRelist = (apply: (found: Profiles | null, held: Profiles | null, shown: string | null) => void) => {
    const signOuts = signOutCount()
    void fetchProfiles().then((found) => {
      if (!open.current || signOutCount() !== signOuts) return
      apply(found, latest.current.profiles, latest.current.chosen)
    })
  }
  // A developer made a company in company setup (#409): once the list is read again (so the switcher names it), it is
  // remembered as a switch is and the app opens afresh under it on the Dossier, focus on its heading, with the Goal
  // chosen in setup for its first pass (#447). A list that can't be read again gains it, so the switcher names it still.
  const created = (profile: ProfileSummary, goal: Goal) => afterRelist((found, held) => {
    rememberProfile(profile.id)
    setListing({ status: 'read', profiles: withProfile(found ?? held, profile) })
    setFellBack(false)
    setSwitched(false)
    setRemovedNote(null)
    setArrival({ goal })
    toRoot()
    choose(profile.id)
  })
  // A developer removed a company made in setup (#418): the list is read again (or, when it can't be, the list held
  // less it), so the switcher drops it. Removing another company changes only the list. Removing the one shown forgets
  // it and opens the default (or the first listed; with neither, no profile named, so the API's default answers) afresh
  // where the developer is, the top bar saying the profile is no longer available and the developer page what was
  // removed.
  const removed = (id: string) => afterRelist((found, held, shown) => {
    const list = found ?? withoutProfile(held, id)
    setListing({ status: 'read', profiles: list })
    if (shown !== id) return
    rememberProfile(null)
    const start = startingProfile(list, id)
    setFellBack(start.fellBack)
    setSwitched(false)
    setRemovedNote(held?.profiles.find((p) => p.id === id)?.company ?? id)
    choose(start.id)
  })
  useEffect(() => {
    let current = true
    void fetchProfiles().then((found) => {
      if (!current) return
      const start = startingProfile(found, rememberedProfile())
      if (start.fellBack) rememberProfile(null) // said once; the next reload opens the default quietly
      if (start.id) choose(start.id)
      setFellBack(start.fellBack)
      setListing({ status: 'read', profiles: found })
    })
    return () => {
      current = false
    }
  }, [])
  const forgetRemovedNote = useCallback(() => setRemovedNote(null), [])
  useEffect(() => () => setProfile(null), []) // signing out leaves no profile for the next sign-in

  if (listing.status === 'reading') return <TopBar />
  const switcher = listing.profiles && chosen
    ? <ProfileSwitcher profiles={listing.profiles.profiles} selected={chosen} onSwitch={switchTo} fellBack={fellBack}
      switched={switched} />
    : undefined
  const company = listing.profiles?.profiles.find((p) => p.id === chosen)?.company
  return <App key={`${chosen ?? ''}:${freshStarts}`} onSignOut={onSignOut} profileSwitcher={switcher}
    userName={userName} company={company} onStartedAfresh={startedAfresh} onCompanyCreated={created} onCompanyRemoved={removed}
    removedCompany={removedNote ?? undefined} onRemovedShown={forgetRemovedNote} arrival={arrival}
    onArrivalUsed={arrivalUsed} />
}

/** Puts the Dossier's address in the address bar, as a new history entry, unless it is there already. */
function toRoot() {
  if (window.location.pathname !== '/') window.history.pushState(null, '', '/')
}
