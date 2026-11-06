import { readStored, store } from './stored.ts'

/** The pinned chat's width (#231; design.md, Layout: over 1000px): 480px unless the lead drags it, from 360px to
 * 720px, always leaving the page at least 560px, and remembered in this browser. */

/** The default width; app.css falls back to the same 480px before AskVouch sets --chat-width. */
export const CHAT_WIDTH = 480
const NARROWEST = 360
const WIDEST = 720
const PAGE_KEEPS = 560
/** One arrow-key press on the resize handle. */
export const CHAT_WIDTH_STEP = 32

const REMEMBERED = 'vouch.chatWidth'

/** The narrowest and widest the pinned chat may be. */
export type ChatWidthRange = { min: number; max: number }

/** The window's width as the page lays out in it: the root's client width, which leaves out a scrollbar, so the page
 * really keeps its 560px. The one source for every width check. */
export const pageWidth = () => document.documentElement.clientWidth

/** The range for a window this wide. */
export function chatWidthRange(windowWidth: number): ChatWidthRange {
  return { min: NARROWEST, max: Math.max(NARROWEST, Math.min(WIDEST, windowWidth - PAGE_KEEPS)) }
}

/** `width` kept within `range`. */
export function clampChatWidth(width: number, { min, max }: ChatWidthRange): number {
  return Math.min(max, Math.max(min, Math.round(width)))
}

/** The width this browser remembers, as chosen (clamped only when shown, so a narrow window doesn't lose it); 480px
 * when none is kept or it isn't a number. */
export function rememberedChatWidth(): number {
  const stored = readStored(REMEMBERED)?.trim()
  const width = stored ? Number(stored) : NaN
  return Number.isFinite(width) ? width : CHAT_WIDTH
}

/** Keeps `width` for the next pin, page or visit; storage refused, it holds for the page's life only. */
export function rememberChatWidth(width: number) {
  store(REMEMBERED, String(width))
}
