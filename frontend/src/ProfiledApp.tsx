import { useEffect, useState } from 'react'
import App from './App.tsx'
import { mayLeave } from './leaving.ts'
import { fetchProfiles, type Profiles, type ProfileSummary, startingProfile } from './profiles.ts'
import ProfileSwitcher from './ProfileSwitcher.tsx'
import { rememberedProfile, rememberProfile, setProfile } from './session.ts'
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
 * the list read again so the switcher names it.
 */
export default function ProfiledApp({ onSignOut, userName }: { onSignOut?: () => void; userName?: string }) {
  const [listing, setListing] = useState<Listing>({ status: 'reading' })
  const [chosen, setChosen] = useState<string | null>(null)
  const [fellBack, setFellBack] = useState(false)
  const [switched, setSwitched] = useState(false) // the user switched: the dropdown takes focus again
  const [freshStarts, setFreshStarts] = useState(0) // each fresh start opens the app afresh
  // Opens the app under `id`: at start (the remembered or the default profile) and on each switch.
  const choose = (id: string) => {
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
    choose(id)
  }
  // The lead started the profile afresh (#285): the app opens again on the Overview, as after a switch.
  const startedAfresh = () => {
    if (window.location.pathname !== '/') window.history.pushState(null, '', '/')
    setFreshStarts((n) => n + 1)
  }
  // A developer made a company in company setup (#409): remembered as a switch is, and the app opens afresh under it on
  // the Dossier, the list read again so the switcher names it. A list that can't be read again gains it, so the
  // switcher names it still.
  const created = (profile: ProfileSummary) => {
    rememberProfile(profile.id)
    if (window.location.pathname !== '/') window.history.pushState(null, '', '/')
    void fetchProfiles().then((found) => {
      setListing((was) => {
        const held = found ?? (was.status === 'read' ? was.profiles : null) ?? { profiles: [], default: null }
        const listed = held.profiles.some((p) => p.id === profile.id) ? held
          : { ...held, profiles: [...held.profiles, profile] }
        return { status: 'read', profiles: listed }
      })
      setFellBack(false)
      choose(profile.id)
    })
  }
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
  useEffect(() => () => setProfile(null), []) // signing out leaves no profile for the next sign-in

  if (listing.status === 'reading') return <TopBar />
  const switcher = listing.profiles && chosen
    ? <ProfileSwitcher profiles={listing.profiles.profiles} selected={chosen} onSwitch={switchTo} fellBack={fellBack}
      switched={switched} />
    : undefined
  const company = listing.profiles?.profiles.find((p) => p.id === chosen)?.company
  return <App key={`${chosen ?? ''}:${freshStarts}`} onSignOut={onSignOut} profileSwitcher={switcher}
    userName={userName} company={company} onStartedAfresh={startedAfresh} onCompanyCreated={created} />
}
