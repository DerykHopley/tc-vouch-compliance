import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8')
const css = read('./tokens.css')
const design = read('../../../docs/design.md')

/** The custom properties declared in the first block whose selector starts with `selector`. */
function block(selector: string): Map<string, string> {
  const start = css.indexOf(selector)
  expect(start, `no block for ${selector}`).toBeGreaterThanOrEqual(0)
  const body = css.slice(css.indexOf('{', start) + 1, css.indexOf('}', start))
  return new Map([...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]))
}

const light = block(':root {')
const darkBySystem = block('@media screen and (prefers-color-scheme: dark)')
const darkBySwitch = block(':root[data-theme="dark"]')

describe('tokens.css', () => {
  it('declares every token docs/design.md names', () => {
    const table = design.slice(design.indexOf('## Tokens'), design.indexOf('## Layout'))
    const named = new Set([...table.matchAll(/`(--[\w-]+)`/g)].map((m) => m[1]))
    for (const verdict of ['--evidenced', '--partly-evidenced', '--not-evidenced', '--not-applicable']) {
      named.add(`${verdict}-bg`)
    }
    expect(named.size).toBeGreaterThan(15)
    expect([...named].filter((name) => !light.has(name))).toEqual([])
  })

  it('redefines every colour token for dark, the same way for the system setting and the switch', () => {
    const colours = [...light.keys()].filter((name) => !name.startsWith('--f-'))
    expect(colours.filter((name) => !darkBySwitch.has(name))).toEqual([])
    expect(Object.fromEntries(darkBySystem)).toEqual(Object.fromEntries(darkBySwitch))
  })

  it('gives each typeface a real fallback', () => {
    for (const font of ['--f-display', '--f-body', '--f-mono', '--f-quote']) {
      expect(light.get(font)).toMatch(/, (serif|sans-serif|monospace)$/)
    }
  })
})
