/** The pinned chat's width (#231; design.md, Layout: over 1000px): 480px unless the lead drags it, from 360px to
 * 720px, always leaving the page at least 560px, and remembered in this browser. */

export const CHAT_WIDTH = 480
const NARROWEST = 360
const WIDEST = 720
const PAGE_KEEPS = 560
/** One arrow-key press on the resize handle. */
export const CHAT_WIDTH_STEP = 32

// Storage can be missing or refused (a private window, blocked site data): then the width lasts the page's life.
const REMEMBERED = 'vouch.chatWidth'

/** The narrowest and widest the pinned chat may be in a window this wide. */
export function chatWidthRange(windowWidth: number): { min: number; max: number } {
  return { min: NARROWEST, max: Math.max(NARROWEST, Math.min(WIDEST, windowWidth - PAGE_KEEPS)) }
}

/** `width` kept within the range for a window this wide. */
export function clampChatWidth(width: number, windowWidth: number): number {
  const { min, max } = chatWidthRange(windowWidth)
  return Math.min(max, Math.max(min, Math.round(width)))
}

/** The width this browser remembers, clamped to the window; 480px when none is kept or it isn't a number. */
export function rememberedChatWidth(windowWidth: number): number {
  let stored: string | null = null
  try {
    stored = window.localStorage.getItem(REMEMBERED)
  } catch {
    // nothing kept
  }
  const width = stored === null || stored.trim() === '' ? NaN : Number(stored)
  return clampChatWidth(Number.isFinite(width) ? width : CHAT_WIDTH, windowWidth)
}

/** Keeps `width` for the next pin, page or visit. */
export function rememberChatWidth(width: number) {
  try {
    window.localStorage.setItem(REMEMBERED, String(width))
  } catch {
    // not kept: the width still holds while the page is open
  }
}
