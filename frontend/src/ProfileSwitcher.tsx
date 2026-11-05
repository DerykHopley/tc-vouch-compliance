import { useId } from 'react'
import { type ProfileSummary, profileLabel } from './profiles.ts'

type Props = { profiles: ProfileSummary[]; selected: string; onSwitch: (id: string) => void }

/** The top bar's profile switcher (#47; the prototype's, branch prototype/ui, variant E): a menu of the user's
 * profiles; with one, its name and no menu. */
export default function ProfileSwitcher({ profiles, selected, onSwitch }: Props) {
  const id = useId()
  if (profiles.length === 1) {
    return (
      <div className="prof">
        <span className="lbl">Profile</span>
        <span>{profileLabel(profiles[0])}</span>
      </div>
    )
  }
  return (
    <div className="prof">
      <label className="lbl" htmlFor={id}>Profile</label>
      <select id={id} className="field" value={selected} onChange={(e) => onSwitch(e.target.value)}>
        {profiles.map((p) => <option key={p.id} value={p.id}>{profileLabel(p)}</option>)}
      </select>
    </div>
  )
}
