import { useEffect, useState } from 'react'
import App from './App.tsx'
import { fetchProfiles, type Profiles } from './profiles.ts'
import ProfileSwitcher from './ProfileSwitcher.tsx'
import { setProfile } from './session.ts'

/**
 * The app under one compliance profile at a time (#47), with the profile switcher in its top bar. Switching names the
 * profile on every API call and opens the app afresh, so nothing the page holds (overview, working paper, run, chat)
 * carries over from the profile before. Until a profile is chosen, calls name none and the API's default answers.
 */
export default function Profiled({ onSignOut }: { onSignOut?: () => void }) {
  const [listing, setListing] = useState<Profiles | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)
  const choose = (id: string) => {
    setProfile(id) // before the app opens again, so its first calls name it
    setChosen(id)
  }
  useEffect(() => {
    let current = true
    void fetchProfiles().then((found) => {
      if (!current || !found) return
      setListing(found)
      // The default profile was left out (it can't be loaded): calls naming none would fail, so take the first.
      if (found.default === null && found.profiles.length > 0) choose(found.profiles[0].id)
    })
    return () => {
      current = false
    }
  }, [])
  useEffect(() => () => setProfile(null), []) // signing out leaves no profile for the next sign-in

  const selected = chosen ?? listing?.default
  const switcher = listing && selected
    ? <ProfileSwitcher profiles={listing.profiles} selected={selected} onSwitch={choose} />
    : undefined
  return <App key={chosen ?? ''} onSignOut={onSignOut} profileSwitcher={switcher} />
}
