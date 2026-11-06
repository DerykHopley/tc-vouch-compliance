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
// A bulleted list written on one line after a colon, as the models do: "What exists: - one - two", also after a bold
// or italic lead-in ("**M1 (must):** - one - two"). Numbers on such a line stay text: "Stand: 1. Januar 2026, geändert
// am 2. Februar 2026." is a sentence, not a list (#218).
const INLINE_BULLETS = /^(.*?:\**)\s+-\s+(.+)$/

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

/** A line as the lines the models meant: an inline bulleted list after a colon becomes its lead-in and one line per
 * item. */
function unfold(line: string): string[] {
  const bullets = INLINE_BULLETS.exec(line)
  if (bullets) {
    const items = bullets[2].split(/\s+-\s+/)
    if (items.length > 1) return [bullets[1], ...items.map((item) => `- ${item}`)]
  }
  return [line]
}

// A lead-in line ending in a colon, also inside bold or italics ("Steps:", "**Next steps:**").
const LEAD_IN = /:\**\s*$/

/**
 * A numbered line's item, and whether it starts a new list. A list starts at 1 on the line after a lead-in ending in
 * a colon, and goes on with each next number. Null otherwise: "15. März 2026 …", "1. Januar …" or "5. The policy …"
 * on a line of its own stays text, as written (#218).
 */
function numberedItem(text: string, number: number, open: Block | null, afterLeadIn: boolean) {
  if (number === 1 && afterLeadIn) return { text, starts: true }
  if (open?.kind === 'ol' && number === open.items.length + 1) return { text, starts: false }
  return null
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
  let previous = '' // the last line shown, across blank lines
  for (const line of text.split('\n').flatMap(unfold)) {
    if (!line.trim()) {
      open = null // a blank line ends the paragraph or list
      continue
    }
    const bullet = BULLET.exec(line)
    const marked = bullet ? null : NUMBERED.exec(line)
    // A bare marker ("- " before a cite the chat splits out) has no item to show: no empty bullet.
    if ((bullet?.[1] ?? marked?.[2])?.trim() === '') continue
    const numbered = marked && numberedItem(marked[2], Number(marked[1]), open, LEAD_IN.test(previous))
    previous = line
    if (numbered?.starts) open = null // a list after a lead-in is a new list
    if (bullet) add('ul', runs(bullet[1]))
    else if (numbered) add('ol', runs(numbered.text))
    else add('p', runs(line))
  }
  return blocks
}
