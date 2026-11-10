import { afterEach, describe, expect, it, vi } from 'vitest'
import { canPinChat, chatWidthRange, clampChatWidth, PIN_FROM, rememberChatWidth, rememberedChatWidth } from './chatWidth.ts'

describe('the pinned chat width (ticket 231)', () => {
  it('runs from 360px to 720px in a 1800px window', () => {
    const range = chatWidthRange(1800)
    expect(range).toEqual({ min: 360, max: 720 })
    expect(clampChatWidth(200, range)).toBe(360)
    expect(clampChatWidth(900, range)).toBe(720)
    expect(clampChatWidth(500, range)).toBe(500)
  })

  it('leaves the page the 1042px the working paper needs (ticket 465): at 1500px the widest is 458px', () => {
    expect(chatWidthRange(1500)).toEqual({ min: 360, max: 458 })
    expect(clampChatWidth(700, chatWidthRange(1500))).toBe(458)
  })

  it('never goes under 360px, even in a window too narrow to leave the page its width', () => {
    expect(chatWidthRange(800)).toEqual({ min: 360, max: 360 })
  })
})

describe('the pin rule (ticket 465)', () => {
  it('offers pinning from 1402px: the narrowest chat, 360px, beside the working paper\'s 1042px', () => {
    expect(PIN_FROM).toBe(1402)
    expect(canPinChat(1401)).toBe(false)
    expect(canPinChat(1402)).toBe(true)
    expect(canPinChat(1403)).toBe(true)
  })

  it('at the threshold the page keeps its 1042px beside the narrowest chat', () => {
    expect(PIN_FROM - chatWidthRange(PIN_FROM).max).toBe(1042)
  })

  it('offers no pin at 1024px or in a phone-width window', () => {
    expect(canPinChat(1024)).toBe(false)
    expect(canPinChat(390)).toBe(false)
  })
})

describe('the remembered chat width', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  it('is 480px when nothing is stored, or what is stored is not a number', () => {
    expect(rememberedChatWidth()).toBe(480)
    window.localStorage.setItem('vouch.chatWidth', 'abc')
    expect(rememberedChatWidth()).toBe(480)
    window.localStorage.setItem('vouch.chatWidth', ' ')
    expect(rememberedChatWidth()).toBe(480)
  })

  it('comes back as chosen, unclamped: the window clamps it when shown', () => {
    window.localStorage.setItem('vouch.chatWidth', '5000')
    expect(rememberedChatWidth()).toBe(5000)
  })

  it('reads back what was remembered', () => {
    rememberChatWidth(612)
    expect(rememberedChatWidth()).toBe(612)
  })

  it('still works when storage is refused', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } })
    expect(() => rememberChatWidth(600)).not.toThrow()
    expect(rememberedChatWidth()).toBe(480)
  })
})
