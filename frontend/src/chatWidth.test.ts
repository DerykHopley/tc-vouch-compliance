import { afterEach, describe, expect, it, vi } from 'vitest'
import { chatWidthRange, clampChatWidth, rememberChatWidth, rememberedChatWidth } from './chatWidth.ts'

describe('the pinned chat width (ticket 231)', () => {
  it('runs from 360px to 720px in a 1400px window', () => {
    expect(chatWidthRange(1400)).toEqual({ min: 360, max: 720 })
    expect(clampChatWidth(200, 1400)).toBe(360)
    expect(clampChatWidth(900, 1400)).toBe(720)
    expect(clampChatWidth(500, 1400)).toBe(500)
  })

  it('leaves the page at least 560px: at 1100px the widest is 540px', () => {
    expect(chatWidthRange(1100)).toEqual({ min: 360, max: 540 })
    expect(clampChatWidth(700, 1100)).toBe(540)
  })

  it('never goes under 360px, even in a window too narrow to leave the page 560px', () => {
    expect(chatWidthRange(800)).toEqual({ min: 360, max: 360 })
  })
})

describe('the remembered chat width', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  it('is 480px when nothing is stored, or what is stored is not a number', () => {
    expect(rememberedChatWidth(1400)).toBe(480)
    window.localStorage.setItem('vouch.chatWidth', 'abc')
    expect(rememberedChatWidth(1400)).toBe(480)
  })

  it('is clamped to the window: 5000 opens at the widest allowed', () => {
    window.localStorage.setItem('vouch.chatWidth', '5000')
    expect(rememberedChatWidth(1400)).toBe(720)
    expect(rememberedChatWidth(1100)).toBe(540)
  })

  it('reads back what was remembered', () => {
    rememberChatWidth(612)
    expect(rememberedChatWidth(1400)).toBe(612)
  })

  it('still works when storage is refused', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } })
    expect(() => rememberChatWidth(600)).not.toThrow()
    expect(rememberedChatWidth(1400)).toBe(480)
  })
})
