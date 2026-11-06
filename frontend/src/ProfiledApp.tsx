import { useEffect, useState } from 'react'
import App from './App.tsx'
import { fetchProfiles, type Profiles, startingProfile } from './profiles.ts'
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
 * still listed; otherwise the default, and the top bar says so (#201).
 */
export default function ProfiledApp({ onSignOut, userName }: { onSignOut?: () => void; userName?: string }) {
  const [listing, setListing] = useState<Listing>({ status: 'reading' })
  const [chosen, setChosen] = useState<string | null>(null)
  const [fellBack, setFellBack] = useState(false)
  const [switched, setSwitched] = useState(false) // the user switched: the dropdown takes focus again
  // Opens the app under `id`: at start (the remembered or the default profile) and on each switch.
  const choose = (id: string) => {
    setProfile(id) // before the app opens again, so its first calls name it
    setChosen(id)
  }
  // The user's switch in the menu: remembered for the next reload, and it ends any fallback note.
  const switchTo = (id: string) => {
    rememberProfile(id)
    setFellBack(false)
    setSwitched(true)
    choose(id)
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
  return <App key={chosen ?? ''} onSignOut={onSignOut} profileSwitcher={switcher} userName={userName} />
}
