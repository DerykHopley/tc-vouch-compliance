import { plural } from './assessment.ts'

/**
 * The guard on leaving a working paper with overrides not yet recorded (#182). The paper reports how many it holds
 * (saved, no decision yet); each way out (App's views, Back and Forward, a reload, ProfiledApp's profile switch) asks
 * with the browser's own confirm before it drops them. A module rather than props, as ProfiledApp sits above App and
 * the paper.
 */
let unsaved = 0

/** The paper's count of overrides not yet recorded; 0 when it holds none or is gone. */
export function setUnsaved(count: number) {
  unsaved = count
}

/** The count reported, for the reload and close prompt, which shows the browser's own words. */
export const unsavedCount = () => unsaved

/** What the ask says. */
export const leaveQuestion = (count: number) =>
  `You have ${plural(count, 'override')} not yet recorded. Leave and drop ${count === 1 ? 'it' : 'them'}?`

/** True when nothing is unsaved; else the lead's answer to the ask. */
export const mayLeave = () => unsaved === 0 || window.confirm(leaveQuestion(unsaved))
