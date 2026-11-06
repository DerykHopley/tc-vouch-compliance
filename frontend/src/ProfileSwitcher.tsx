import Menu, { MenuItem } from './Menu.tsx'
import { profileLabel, type ProfileSummary, profileTag } from './profiles.ts'

type Props = {
  profiles: ProfileSummary[]
  selected: string
  onSwitch: (id: string) => void
  fellBack?: boolean
  /** The user just switched: the app opened afresh, so focus goes back to the dropdown's button. */
  switched?: boolean
}

/** What the profile button shows: the company, its tag ("DE · EN") and, with a menu, a chevron. */
function Shown({ profile, chevron }: { profile: ProfileSummary; chevron: boolean }) {
  return (
    <>
      <span className="prof-company">{profile.company}</span>
      <span className="prof-tag">{profileTag(profile)}</span>
      {chevron && <span className="chevron" aria-hidden="true">▾</span>}
    </>
  )
}

/** The top bar's profile switcher (#47), as a bordered dropdown button (#222, the owner's option D): the company, a
 * tag for its country and answer language, and a chevron; its menu names each profile in full, the one shown checked.
 * With one profile, its name and no menu. `fellBack`: the profile remembered from before is no longer listed, so a
 * note names the one shown instead (#201). */
export default function ProfileSwitcher({ profiles, selected, onSwitch, fellBack = false, switched = false }: Props) {
  const shown = profiles.find((p) => p.id === selected) ?? profiles[0]
  const note = fellBack && shown
    ? <span className="prof-note" role="status">
        Your last profile is no longer available: showing {shown.company}.
      </span>
    : null
  if (!shown) return null
  return (
    <div className="prof">
      {profiles.length === 1
        ? (
          <span className="prof-button" role="group" aria-label={`Profile: ${profileLabel(shown)}`}>
            <Shown profile={shown} chevron={false} />
          </span>
        )
        : (
          <Menu label={`Profile: ${profileLabel(shown)}`} buttonClassName="prof-button" focusOnMount={switched}
            button={<Shown profile={shown} chevron />}>
            {(close) => profiles.map((p) => (
              <MenuItem key={p.id} checked={p.id === selected} onSelect={() => {
                close()
                if (p.id !== selected) onSwitch(p.id)
              }}>{profileLabel(p)}</MenuItem>
            ))}
          </Menu>
        )}
      {note}
    </div>
  )
}
