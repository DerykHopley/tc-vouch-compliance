import { Fragment, type ReactNode } from 'react'
import Menu, { MenuItem } from './Menu.tsx'
import { startAfresh } from './profiles.ts'
import { initials, LOCAL_USER, type Theme } from './user.ts'

type Props = {
  /** The signed-in user's name; none in local mode. */
  name?: string
  theme: Theme
  onTheme: () => void
  /** Clerk mode's sign out; local mode has nobody to sign out. */
  onSignOut?: () => void
  /** The company of the profile shown, for the fresh start's words; none without a profile list. */
  company?: string
  /** The fresh start went through (#285): the app opens afresh. Without it, the menu offers no fresh start. */
  onStartedAfresh?: () => void
  /** A developer's tools (#495, M9 decision 4): Developer page and Set up a company; none for a lead. Choosing one
   * closes the menu, focus back on its button, then opens the page (the app moves focus on where that button hides). */
  developerTools?: { onPage: () => void; onSetup: () => void }
}

const RUNNING = 'An assessment is running: start afresh once it has finished.'
const FAILED = 'Vouch could not start afresh just now. Try again.'

/** "Start <company> afresh", or "Start afresh" without a profile list: the menu item's words and the confirm's. */
const startWords = (company: string | undefined) => company ? `Start ${company} afresh` : 'Start afresh'

/** Asks with the browser's own confirm, then starts the profile's progress afresh (#285); a refusal says why in an
 * alert, and the page stays as it was. A 401 has signed out already: no alert. */
async function confirmFreshStart(company: string | undefined, onStarted: () => void) {
  const question = `${startWords(company)}? Your progress here starts from nothing; the audit trail keeps every entry.`
  if (!window.confirm(question)) return
  const result = await startAfresh()
  if (result === 'started') onStarted()
  else if (result === 'running') window.alert(RUNNING)
  else if (result === 'failed') window.alert(FAILED)
}

/** The user menu behind the user's initials (#222), in the Letterhead order (#495, option A): "Signed in as <name>" and
 * the theme; Developer page and Set up a company (developers only); Sign out; the fresh start (#285), last. A separator
 * splits the groups (the arrow keys skip it), and a group with nothing in it leaves none. In local mode, with no
 * sign-in, no "Signed in as" or Sign out. */
export default function UserMenu({ name, theme, onTheme, onSignOut, company, onStartedAfresh, developerTools }:
  Props) {
  const signedIn = Boolean(onSignOut && name)
  // Local mode's one user is "Local user"; in clerk mode, until Clerk has read the user, an ellipsis, never "LU".
  const shown = onSignOut ? name : LOCAL_USER
  return (
    <Menu label={signedIn ? `${name}: your menu` : 'Your menu'} buttonClassName="user-button" className="user-menu"
      button={<span aria-hidden="true">{shown ? initials(shown) : '…'}</span>}>
      {(close) => {
        const you = (
          <>
            {signedIn && <p className="menu-note" role="none">Signed in as {name}</p>}
            <MenuItem onSelect={() => { onTheme(); close() }}>{theme === 'light' ? 'Dark theme' : 'Light theme'}</MenuItem>
          </>
        )
        const tools = developerTools && (
          <>
            <MenuItem onSelect={() => { close(); developerTools.onPage() }}>Developer page</MenuItem>
            <MenuItem onSelect={() => { close(); developerTools.onSetup() }}>Set up a company</MenuItem>
          </>
        )
        const signOut = onSignOut && <MenuItem onSelect={() => { close(); onSignOut() }}>Sign out</MenuItem>
        const reset = onStartedAfresh && (
          <MenuItem onSelect={() => { close(); void confirmFreshStart(company, onStartedAfresh) }}>
            {`${startWords(company)}…`}
          </MenuItem>
        )
        // Each group keyed by its name, so a group arriving while the menu is open (the developer's, once the settings
        // are read) moves no item into another's place, and the focused item stays the one focused.
        const groups = ([['you', you], ['tools', tools], ['sign-out', signOut], ['reset', reset]] as [string, ReactNode][])
          .filter(([, items]) => items)
        return groups.map(([key, items], i) => (
          <Fragment key={key}>
            {i > 0 && <div className="menu-separator" role="separator" />}
            {items}
          </Fragment>
        ))
      }}
    </Menu>
  )
}
