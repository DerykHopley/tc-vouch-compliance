import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { type Block, proseBlocks } from './prose.ts'

/**
 * Prose over real lines (issue 236): every profile under fixtures/ (each folder with a profile.yaml), its policies,
 * its records and its expected answers' `why` text, read when the test runs, so a new profile's language brings its
 * own cases with no edit here. Each line on its own renders with no list and no emphasis unless it really has the
 * Markdown for them; a `#` line may be a heading. Prose was built on English strings and once turned "15. März" into a
 * numbered item and italicised "Mitarbeiter*innen" (ticket 218).
 *
 * Read from the working directory (npm test runs in frontend/), never through import.meta.url (issue 194).
 */
const FIXTURES = join(process.cwd(), '..', 'fixtures')

const PROFILES = readdirSync(FIXTURES, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && existsSync(join(FIXTURES, entry.name, 'profile.yaml')))
  .map((entry) => entry.name)
  .sort()

/** Each non-empty line of a profile's documents and `why` answers, with where it came from. */
function corpus(profile: string): { where: string; line: string }[] {
  const found: { where: string; line: string }[] = []
  for (const folder of ['policies', 'records']) {
    const dir = join(FIXTURES, profile, folder)
    if (!existsSync(dir)) continue
    for (const file of readdirSync(dir).filter((name) => name.endsWith('.md')).sort()) {
      readFileSync(join(dir, file), 'utf8').split('\n').forEach((line, i) => {
        if (line.trim()) found.push({ where: `${profile}/${folder}/${file}:${i + 1}`, line })
      })
    }
  }
  const answers = join(FIXTURES, profile, 'expected-answers.yaml')
  if (existsSync(answers)) {
    readFileSync(answers, 'utf8').split('\n').forEach((line, i) => {
      const why = /^\s*why:\s*(.+)$/.exec(line)
      if (why) found.push({ where: `${profile}/expected-answers.yaml:${i + 1}`, line: why[1].replace(/^(["'])(.*)\1$/, '$2') })
    })
  }
  return found
}

const BULLET = /^\s*[-*•]\s+\S/
const HEADING = /^\s*#{1,3}\s+\S/
const BOLD = /\*\*\S(?:.*?\S)?\*\*/
const ITALIC = /(?<![\p{L}\p{N}*])\*[^\s*](?:[^*]*[^\s*])?\*(?![\p{L}\p{N}*])/u

/** What the line may render as, from its own Markdown markers; empty when it renders as it should. */
function surprises(line: string, blocks: Block[]): string[] {
  const found: string[] = []
  for (const block of blocks) {
    if (block.kind === 'ol') found.push('a numbered list')
    if (block.kind === 'ul' && !BULLET.test(line)) found.push('a bulleted list')
    if (block.kind === 'h' && !HEADING.test(line) && !BOLD.test(line)) found.push('a heading')
    const runs = block.kind === 'h' ? [block.runs] : block.kind === 'p' ? block.lines : block.items
    for (const run of runs.flat()) {
      if (run.bold && !BOLD.test(line)) found.push(`bold "${run.text}"`)
      if (run.italic && !ITALIC.test(line)) found.push(`italic "${run.text}"`)
    }
  }
  return found
}

describe('Prose over every profile\'s real lines (issue 236)', () => {
  it('reads every profile under fixtures/', () => {
    expect(PROFILES).toEqual(expect.arrayContaining(['alpenwerk', 'nordlicht']))
    for (const profile of PROFILES) expect(corpus(profile).length, profile).toBeGreaterThan(50)
  })

  it.each(PROFILES)('renders each of %s\'s lines with no list or emphasis it doesn\'t write', (profile) => {
    const wrong = corpus(profile)
      .map(({ where, line }) => ({ where, line, found: surprises(line, proseBlocks(line)) }))
      .filter(({ found }) => found.length > 0)
      .map(({ where, line, found }) => `${where}: ${found.join(', ')}: ${line}`)
    expect(wrong).toEqual([])
  })
})

describe('Prose over lines the fixtures lack (issue 236)', () => {
  // German dates, gender stars, colons, ISA numbering and an English section number, each a line of its own.
  it.each([
    '15. März 2026: Die Richtlinie wurde freigegeben.',
    '1. Januar 2026 tritt die Fassung 4.0 in Kraft.',
    'Stand: 1. Januar 2026, geändert am 2. Februar 2026.',
    'Alle Mitarbeiter*innen und Lieferant*innen werden geschult.',
    'Die Prüfer*innen und die*der Beauftragte bestätigen es.',
    '5.1.1 Inwieweit werden Informationswerte identifiziert?',
    'Section 5. The policy names its owner.',
    'Zuständig: IT-Leitung, Datenschutz und Personal.',
  ])('renders "%s" as written', (line) => {
    expect(surprises(line, proseBlocks(line))).toEqual([])
    expect(proseBlocks(line)).toEqual([{ kind: 'p', lines: [[{ text: line }]] }])
  })

  it('still reads a real list after a colon and a newline, and real bold', () => {
    expect(proseBlocks('Offene Punkte:\n1. Die Risikobewertung\n2. Der Nachweis')).toEqual([
      { kind: 'p', lines: [[{ text: 'Offene Punkte:' }]] },
      { kind: 'ol', items: [[{ text: 'Die Risikobewertung' }], [{ text: 'Der Nachweis' }]] },
    ])
    expect(proseBlocks('Es fehlt **die Freigabe** der Leitung.')).toEqual([
      { kind: 'p', lines: [[{ text: 'Es fehlt ' }, { text: 'die Freigabe', bold: true }, { text: ' der Leitung.' }]] },
    ])
  })

  it('still reads the corpus\'s own Markdown: a bulleted line as a list item, a # line as a heading', () => {
    const bullets = PROFILES.flatMap(corpus).filter(({ line }) => BULLET.test(line))
    expect(bullets.length).toBeGreaterThan(0)
    for (const { line } of bullets) expect(proseBlocks(line).map((b) => b.kind)).toContain('ul')
    const headings = PROFILES.flatMap(corpus).filter(({ line }) => HEADING.test(line))
    expect(headings.length).toBeGreaterThan(0)
    for (const { line } of headings) expect(proseBlocks(line).map((b) => b.kind)).toEqual(['h'])
  })
})
