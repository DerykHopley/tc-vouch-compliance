import { useId } from 'react'
import { type ProfileSummary, profileLabel } from './profiles.ts'

type Props = { profiles: ProfileSummary[]; selected: string; onSwitch: (id: string) => void; fellBack?: boolean }

/** The top bar's profile switcher (#47; the prototype's, branch prototype/ui, variant E): a menu of the user's
 * profiles; with one, its name and no menu. `fellBack`: the profile remembered from before is no longer listed, so
 * a note names the one shown instead (#201). */
export default function ProfileSwitcher({ profiles, selected, onSwitch, fellBack = false }: Props) {
  const id = useId()
  const shown = profiles.find((p) => p.id === selected)
  const note = fellBack && shown
    ? <span className="prof-note" role="status">
        Your last profile is no longer available: showing {shown.company}.
      </span>
    : null
  if (profiles.length === 1) {
    return (
      <div className="prof">
        <span className="lbl">Profile</span>
        <span>{profileLabel(profiles[0])}</span>
        {note}
      </div>
    )
  }
  return (
    <div className="prof">
      <label className="lbl" htmlFor={id}>Profile</label>
      <select id={id} className="field" value={selected} onChange={(e) => onSwitch(e.target.value)}>
        {profiles.map((p) => <option key={p.id} value={p.id}>{profileLabel(p)}</option>)}
      </select>
      {note}
    </div>
  )
}
