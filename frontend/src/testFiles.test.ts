import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * #194: under vitest's jsdom environment, Vite rewrites `new URL('<a string literal>', import.meta.url)` (its asset
 * pattern) into the dev server's address, `http://localhost:3000/…` (`/@fs/…` outside frontend/), so `readFileSync`
 * throws ERR_INVALID_URL_SCHEME. A path passed in a variable is left alone. Tests read files by `process.cwd()` (npm
 * test runs in frontend/) or through a variable, never by that literal form.
 */
const LITERAL_URL = /new URL\(\s*(['"`])[^'"`]*\1\s*,\s*import\.meta\.url\s*\)/

/** Every test file under `dir`, as [path, text]. */
function testFiles(dir: string): [string, string][] {
  return readdirSync(dir, { recursive: true, encoding: 'utf8' })
    .filter((f) => /\.test\.tsx?$/.test(f) && !f.endsWith('testFiles.test.ts')) // this file quotes the form
    .map((f) => [f, readFileSync(join(dir, f), 'utf8')])
}

describe('reading files in tests (issue 194)', () => {
  it("finds the line that failed on PR 162, and not the form that works", () => {
    expect(LITERAL_URL.test("readFileSync(new URL('../../tests/roll_up_cases.json', import.meta.url), 'utf8')"))
      .toBe(true)
    expect(LITERAL_URL.test('readFileSync(new URL(rel, import.meta.url), \'utf8\')')).toBe(false)
  })

  it('no test reads a file by new URL with a literal path and import.meta.url', () => {
    const offending = testFiles(join(process.cwd(), 'src')).filter(([, text]) => LITERAL_URL.test(text))
    expect(offending.map(([path]) => path)).toEqual([])
  })
})
