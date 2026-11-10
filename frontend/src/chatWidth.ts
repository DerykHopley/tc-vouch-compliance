import { readStored, store } from './stored.ts'

/** The pinned chat's width (#231; design.md, Layout): 480px unless the lead drags it, from 360px to 720px, always
 * leaving the page the width the working paper needs, and remembered in this browser. Pinning is offered only where
 * the narrowest chat leaves the page that width (#465). */

/** The default width; app.css falls back to the same 480px before AskVouch sets --chat-width. */
export const CHAT_WIDTH = 480
const NARROWEST = 360
const WIDEST = 720
/** The page beside a pinned chat keeps the working paper's width (#465), so nothing in it scrolls sideways and the top
 * bar keeps to one row: its index (250px), the page's two 16px gutters, and the judged requirements table's 760px
 * (app.css, .wp and .reqs.judged: change them together). */
const PAGE_KEEPS = 250 + 2 * 16 + 760
/** The narrowest page width that offers pinning: the narrowest chat beside the page's width, 1402px. */
export const PIN_FROM = PAGE_KEEPS + NARROWEST
/** One arrow-key press on the resize handle. */
export const CHAT_WIDTH_STEP = 32

const REMEMBERED = 'vouch.chatWidth'

/** The narrowest and widest the pinned chat may be. */
export type ChatWidthRange = { min: number; max: number }

/** The window's width as the page lays out in it: the root's client width, which leaves out a scrollbar, so the page
 * really keeps its 560px. The one source for every width check. */
export const pageWidth = () => document.documentElement.clientWidth

/** Whether a window this wide offers pinning (#465): below PIN_FROM the chat stays floating. */
export const canPinChat = (windowWidth: number) => windowWidth >= PIN_FROM

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
