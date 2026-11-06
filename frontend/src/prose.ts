/**
 * The small Markdown subset the models write (#209): bold, italics, bulleted and numbered lists, and paragraphs.
 * It returns plain-text runs for React to show as elements, never HTML: a tag, an image or a link in the model's text
 * stays text, as written.
 */

/** A run of text, bold or italic or neither. */
export type TextRun = { text: string; bold?: true; italic?: true }
/** A paragraph's lines (shown with line breaks between), or a list's items. */
export type Block = { kind: 'p'; lines: TextRun[][] } | { kind: 'ul' | 'ol'; items: TextRun[][] }

// `**bold**`, then `*italic*` (no space just inside the stars, so "2 * 3 * 4" stays as written), only at a word's
// edges: a star inside a word is text, as German gender stars are ("Mitarbeiter*innen", #218).
const EMPHASIS = /(?<![\p{L}\p{N}])(?:\*\*(.+?)\*\*|\*([^\s*](?:[^*]*[^\s*])?)\*)(?![\p{L}\p{N}])/gu
const BULLET = /^\s*[-*•]\s+(.*)$/
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/
// A list written on one line after a colon, as the models do: "What exists: - one - two", "Next: 1. one 2. two",
// also after a bold or italic lead-in ("**M1 (must):** - one - two").
const INLINE_BULLETS = /^(.*?:\**)\s+-\s+(.+)$/
const INLINE_NUMBERED = /^(.*?:\**)\s+1\.\s+(.+)$/

/** The text's runs: bold and italics marked, everything else as written. */
function runs(text: string): TextRun[] {
  const found: TextRun[] = []
  let at = 0
  for (const match of text.matchAll(EMPHASIS)) {
    if (match.index > at) found.push({ text: text.slice(at, match.index) })
    found.push(match[1] !== undefined ? { text: match[1], bold: true } : { text: match[2], italic: true })
    at = match.index + match[0].length
  }
  if (at < text.length) found.push({ text: text.slice(at) })
  return found
}

/** "1. a 2. b 3. c" as its items, while the numbers count up from 1; null when they don't. */
function numberedItems(rest: string): string[] | null {
  const items: string[] = []
  let next = 2
  let text = rest
  for (;;) {
    const marker = new RegExp(`\\s+${next}\\.\\s+`).exec(text)
    if (!marker) break
    items.push(text.slice(0, marker.index))
    text = text.slice(marker.index + marker[0].length)
    next += 1
  }
  items.push(text)
  return items.length > 1 ? items : null
}

/** A line as the lines the models meant: an inline list after a colon becomes its lead-in and one line per item. */
function unfold(line: string): string[] {
  const bullets = INLINE_BULLETS.exec(line)
  if (bullets) {
    const items = bullets[2].split(/\s+-\s+/)
    if (items.length > 1) return [bullets[1], ...items.map((item) => `- ${item}`)]
  }
  const numbered = INLINE_NUMBERED.exec(line)
  const items = numbered && numberedItems(numbered[2])
  if (numbered && items) return [numbered[1], ...items.map((item, i) => `${i + 1}. ${item}`)]
  return [line]
}

/**
 * A numbered line's item, and whether it starts a new list: only at 1 or as the next number of the list above it.
 * Null otherwise, so "15. März 2026 …" stays text (#218).
 */
function numberedItem(line: string, open: Block | null): { text: string; starts: boolean } | null {
  const match = NUMBERED.exec(line)
  if (!match) return null
  const number = Number(match[1])
  const next = open?.kind === 'ol' ? open.items.length + 1 : 1
  if (number !== next && number !== 1) return null
  return { text: match[2], starts: number === 1 }
}

/** The text as paragraphs and lists. */
export function proseBlocks(text: string): Block[] {
  const blocks: Block[] = []
  let open: Block | null = null
  const add = (kind: Block['kind'], content: TextRun[]) => {
    if (open && open.kind === kind) {
      if (open.kind === 'p') open.lines.push(content)
      else open.items.push(content)
      return
    }
    open = kind === 'p' ? { kind, lines: [content] } : { kind, items: [content] }
    blocks.push(open)
  }
  for (const line of text.split('\n').flatMap(unfold)) {
    if (!line.trim()) {
      open = null // a blank line ends the paragraph or list
      continue
    }
    const bullet = BULLET.exec(line)
    const numbered = bullet ? null : numberedItem(line, open)
    const item = bullet ? bullet[1] : numbered?.text
    // A bare marker ("- " before a cite the chat splits out) has no item to show: no empty bullet.
    if (item?.trim() === '') continue
    if (numbered?.starts) open = null // "1." again starts a new list
    if (item === undefined) add('p', runs(line))
    else add(bullet ? 'ul' : 'ol', runs(item))
  }
  return blocks
}
