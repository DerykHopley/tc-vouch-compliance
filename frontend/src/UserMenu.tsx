import Menu, { MenuItem } from './Menu.tsx'
import { initials, LOCAL_USER } from './user.ts'

type Props = {
  /** The signed-in user's name; none in local mode. */
  name?: string
  theme: 'light' | 'dark'
  onTheme: () => void
  /** Clerk mode's sign out; local mode has nobody to sign out. */
  onSignOut?: () => void
}

/** The user menu behind the user's initials (#222, the owner's option D): "Signed in as <name>", the theme, and Sign
 * out; in local mode, with no sign-in, only the theme. */
export default function UserMenu({ name, theme, onTheme, onSignOut }: Props) {
  const signedIn = Boolean(onSignOut && name)
  return (
    <Menu label={signedIn ? `${name}: your menu` : 'Your menu'} buttonClassName="user-button" className="user-menu"
      button={<span aria-hidden="true">{initials(name ?? LOCAL_USER)}</span>}>
      {(close) => (
        <>
          {signedIn && <p className="menu-note" role="none">Signed in as {name}</p>}
          <MenuItem onSelect={() => { onTheme(); close() }}>{theme === 'light' ? 'Dark theme' : 'Light theme'}</MenuItem>
          {onSignOut && <MenuItem onSelect={() => { close(); onSignOut() }}>Sign out</MenuItem>}
        </>
      )}
    </Menu>
  )
}
