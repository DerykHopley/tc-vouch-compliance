/** The running build (issue 580), as `GET /api/health` serves it: the short commit the image was built from ("dev"
 * with none baked in) and Cloud Run's revision (null off Cloud Run). Kept when health is read at start (authConfig.ts),
 * for the user menu's muted last line. */

/** The commit of a build with none baked in: a developer's machine. */
const DEV = 'dev'

let commit = DEV
let revision: string | null = null

/** Keeps health's `build` answer; anything it can't read is "dev" with no revision. */
export function setBuild(answer: unknown) {
  const served = (answer ?? {}) as { commit?: unknown; revision?: unknown }
  commit = typeof served.commit === 'string' && served.commit ? served.commit : DEV
  revision = typeof served.revision === 'string' && served.revision ? served.revision : null
}

/** "Vouch 1a2b3c4 · vouch-00029", "Vouch 1a2b3c4" with no revision, "Vouch dev" locally. */
export function buildLine(): string {
  return revision ? `Vouch ${commit} · ${revision}` : `Vouch ${commit}`
}
