import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import Prose from './Prose.tsx'
import { proseBlocks } from './prose.ts'

afterEach(cleanup)

// The live example (#209): the owner's test on Haiku, a draft answer with its list written inline after a colon.
const LIVE = 'What exists: - Encryption standard 1.2 defines TLS 1.2 or higher for transfers - The standard covers part '
  + 'of the key lifecycle'

describe('proseBlocks', () => {
  it('reads bold and italics as runs of plain text', () => {
    expect(proseBlocks('A **must** and an *optional* one.')).toEqual([
      { kind: 'p', lines: [[{ text: 'A ' }, { text: 'must', bold: true }, { text: ' and an ' },
        { text: 'optional', italic: true }, { text: ' one.' }]] },
    ])
  })

  it('reads lines starting "- " or "1. " as lists, and a blank line as a new paragraph', () => {
    expect(proseBlocks('Found:\n- one\n- two\n\nSteps:\n1. first\n2. second')).toEqual([
      { kind: 'p', lines: [[{ text: 'Found:' }]] },
      { kind: 'ul', items: [[{ text: 'one' }], [{ text: 'two' }]] },
      { kind: 'p', lines: [[{ text: 'Steps:' }]] },
      { kind: 'ol', items: [[{ text: 'first' }], [{ text: 'second' }]] },
    ])
  })

  it('reads a list written inline after a colon, as in the live example', () => {
    expect(proseBlocks(LIVE)).toEqual([
      { kind: 'p', lines: [[{ text: 'What exists:' }]] },
      { kind: 'ul', items: [
        [{ text: 'Encryption standard 1.2 defines TLS 1.2 or higher for transfers' }],
        [{ text: 'The standard covers part of the key lifecycle' }],
      ] },
    ])
    expect(proseBlocks('Next: 1. Name an owner 2. Set a date')).toEqual([
      { kind: 'p', lines: [[{ text: 'Next:' }]] },
      { kind: 'ol', items: [[{ text: 'Name an owner' }], [{ text: 'Set a date' }]] },
    ])
  })

  it('reads an inline list after a bold lead-in, as the chat writes "**M1 (must):** - …"', () => {
    expect(proseBlocks('**What exists:** - Encryption standard 1.2 - Key lifecycle')).toEqual([
      { kind: 'p', lines: [[{ text: 'What exists:', bold: true }]] },
      { kind: 'ul', items: [[{ text: 'Encryption standard 1.2' }], [{ text: 'Key lifecycle' }]] },
    ])
  })

  it('drops a bullet with nothing after it, as a cite split out of a list item leaves', () => {
    expect(proseBlocks('- ')).toEqual([])
    expect(proseBlocks('Found:\n- \n1. ')).toEqual([{ kind: 'p', lines: [[{ text: 'Found:' }]] }])
  })

  it('leaves a German date at a line start as written, not a list item (ticket 218)', () => {
    expect(proseBlocks('15. März 2026 wurde der Vorfall nicht gemeldet.')).toEqual([
      { kind: 'p', lines: [[{ text: '15. März 2026 wurde der Vorfall nicht gemeldet.' }]] },
    ])
    expect(proseBlocks('Antwort:\n15. März 2026 wurde der Vorfall gemeldet.')).toEqual([
      { kind: 'p', lines: [[{ text: 'Antwort:' }], [{ text: '15. März 2026 wurde der Vorfall gemeldet.' }]] },
    ])
  })

  it('reads a numbered list only after a lead-in ending in a colon, then each next number (ticket 218)', () => {
    expect(proseBlocks('Schritte:\n1. erstens\n2. zweitens\n4. März')).toEqual([
      { kind: 'p', lines: [[{ text: 'Schritte:' }]] },
      { kind: 'ol', items: [[{ text: 'erstens' }], [{ text: 'zweitens' }]] },
      { kind: 'p', lines: [[{ text: '4. März' }]] },
    ])
    expect(proseBlocks('**Next steps:**\n\n1. a\n2. b\nThen:\n1. c')).toEqual([
      { kind: 'p', lines: [[{ text: 'Next steps:', bold: true }]] },
      { kind: 'ol', items: [[{ text: 'a' }], [{ text: 'b' }]] },
      { kind: 'p', lines: [[{ text: 'Then:' }]] },
      { kind: 'ol', items: [[{ text: 'c' }]] },
    ])
  })

  it('leaves a numbered line with no lead-in as written, in German and English (ticket 218)', () => {
    const lines = ['1. Januar 2026 trat die Richtlinie in Kraft.', 'Siehe Nr. 3. Die Frist gilt.',
      '5. The policy covers suppliers.', 'See Section 5. The policy covers suppliers.']
    for (const line of lines) {
      expect(proseBlocks(line)).toEqual([{ kind: 'p', lines: [[{ text: line }]] }])
    }
    expect(proseBlocks('The answer.\n1. Januar 2026 trat sie in Kraft.')).toEqual([
      { kind: 'p', lines: [[{ text: 'The answer.' }], [{ text: '1. Januar 2026 trat sie in Kraft.' }]] },
    ])
  })

  it('leaves a star inside a word as written, as German gender stars are (ticket 218)', () => {
    expect(proseBlocks('Alle Mitarbeiter*innen und Lieferant*innen werden geschult.')).toEqual([
      { kind: 'p', lines: [[{ text: 'Alle Mitarbeiter*innen und Lieferant*innen werden geschult.' }]] },
    ])
    for (const line of ['Mitarbeiter *innen und Lieferant*innen', 'Mitarbeiter:innen und Kolleg:innen',
      'Kolleg*innen sind *wichtig']) {
      expect(proseBlocks(line)).toEqual([{ kind: 'p', lines: [[{ text: line }]] }])
    }
    expect(proseBlocks('Mitarbeiter*innen sind *verpflichtet*, **sofort** zu melden.')).toEqual([
      { kind: 'p', lines: [[{ text: 'Mitarbeiter*innen sind ' }, { text: 'verpflichtet', italic: true },
        { text: ', ' }, { text: 'sofort', bold: true }, { text: ' zu melden.' }]] },
    ])
  })

  it('leaves a dash or a number in running text alone', () => {
    expect(proseBlocks('Keys rotate yearly - not monthly - per 2. rule')).toEqual([
      { kind: 'p', lines: [[{ text: 'Keys rotate yearly - not monthly - per 2. rule' }]] },
    ])
  })
})

describe('Prose', () => {
  it('shows bold, italics and lists as elements', () => {
    const { container } = render(<Prose text={'A **must** and *one*.\n- first\n- second'} />)
    expect(container.querySelector('strong')?.textContent).toBe('must')
    expect(container.querySelector('em')?.textContent).toBe('one')
    expect([...container.querySelectorAll('ul > li')].map((li) => li.textContent)).toEqual(['first', 'second'])
  })

  it('shows the live example as a list, not running text', () => {
    const { container } = render(<Prose text={LIVE} />)
    expect(container.querySelectorAll('ul > li')).toHaveLength(2)
  })

  it('shows HTML, a script, an image and a link from model text as plain text', () => {
    const text = '<script>alert(1)</script> <img src=x onerror=alert(2)> [x](javascript:alert(3)) **<b>b</b>**'
    const { container } = render(<Prose text={text} />)
    expect(container.querySelector('script, img, a, b')).toBeNull()
    expect(container.textContent).toBe('<script>alert(1)</script> <img src=x onerror=alert(2)> '
      + '[x](javascript:alert(3)) <b>b</b>')
  })
})
