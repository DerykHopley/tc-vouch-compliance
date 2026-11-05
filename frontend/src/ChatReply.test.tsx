import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import ChatReply from './ChatReply.tsx'
import { replyParts } from './chat.ts'

afterEach(cleanup)

const QUOTE = 'Every company laptop uses full-disk encryption with AES-256.'

describe('replyParts', () => {
  it('splits a reply into its text and its cites', () => {
    expect(replyParts(`It says <cite doc="encryption-standard">${QUOTE}</cite>, as noted.`)).toEqual([
      { text: 'It says ' }, { quote: QUOTE, doc: 'encryption-standard' }, { text: ', as noted.' },
    ])
  })

  it('leaves text with no cite as it is, tags of any other kind included', () => {
    expect(replyParts('A <b>bold</b> claim [encryption-standard].')).toEqual([
      { text: 'A <b>bold</b> claim [encryption-standard].' },
    ])
  })

  it('drops the line break straight after a cite, as the quote is a block of its own', () => {
    expect(replyParts(`- <cite doc="a">${QUOTE}</cite>\n- next`)).toEqual([
      { text: '- ' }, { quote: QUOTE, doc: 'a' }, { text: '- next' },
    ])
  })
})

describe('ChatReply', () => {
  it('shows a cite as a quote, with its document id', () => {
    const { container } = render(<ChatReply text={`It says <cite doc="encryption-standard">${QUOTE}</cite>.`} />)

    const figure = container.querySelector('figure.cite')
    expect(figure?.querySelector('blockquote')?.textContent).toBe(QUOTE)
    expect(figure?.querySelector('figcaption')?.textContent).toBe('encryption-standard')
    expect(container.textContent).toBe(`It says ${QUOTE}encryption-standard.`)
  })

  it('shows HTML or a script inside a cite, or outside one, as text', () => {
    const text = '<b>Bold</b> <cite doc="x"><script>alert(1)</script> <img src=x onerror=alert(2)></cite>'
    const { container } = render(<ChatReply text={text} />)

    expect(container.querySelector('script, img, b')).toBeNull()
    expect(container.querySelector('blockquote')?.textContent).toBe('<script>alert(1)</script> <img src=x onerror=alert(2)>')
    expect(container.textContent).toContain('<b>Bold</b>')
  })
})
