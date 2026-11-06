import { useEffect, useState } from 'react'
import App from './App.tsx'
import { fetchProfiles, type Profiles } from './profiles.ts'
import ProfileSwitcher from './ProfileSwitcher.tsx'
import { setProfile } from './session.ts'
import TopBar from './TopBar.tsx'

/** The profile list as read: not yet, or what came back (null: it couldn't be read). */
type Listing = { status: 'reading' } | { status: 'read'; profiles: Profiles | null }

/**
 * The app under one compliance profile at a time (#47), with the profile switcher in its top bar. The app opens only
 * once the list has answered, under the default profile (or the first listed), so its first calls already name it.
 * Switching names the new profile on every API call and opens the app afresh, so nothing the page holds (overview,
 * working paper, run, chat) carries over from the profile before. Without a list, calls name none and the API's
 * default answers.
 */
export default function ProfiledApp({ onSignOut }: { onSignOut?: () => void }) {
  const [listing, setListing] = useState<Listing>({ status: 'reading' })
  const [chosen, setChosen] = useState<string | null>(null)
  const choose = (id: string) => {
    setProfile(id) // before the app opens again, so its first calls name it
    setChosen(id)
  }
  useEffect(() => {
    let current = true
    void fetchProfiles().then((found) => {
      if (!current) return
      const first = found?.default ?? found?.profiles[0]?.id
      if (first) choose(first)
      setListing({ status: 'read', profiles: found })
    })
    return () => {
      current = false
    }
  }, [])
  useEffect(() => () => setProfile(null), []) // signing out leaves no profile for the next sign-in

  if (listing.status === 'reading') return <TopBar />
  const switcher = listing.profiles && chosen
    ? <ProfileSwitcher profiles={listing.profiles.profiles} selected={chosen} onSwitch={choose} />
    : undefined
  return <App key={chosen ?? ''} onSignOut={onSignOut} profileSwitcher={switcher} />
}
