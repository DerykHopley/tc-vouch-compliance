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
