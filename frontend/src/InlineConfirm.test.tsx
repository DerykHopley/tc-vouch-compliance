import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import InlineConfirm from './InlineConfirm.tsx'

/** The in-app ask (#441), shared by Change goal, Close round and Assess again on a review in progress (#437). */

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const QUESTION = 'Change this to that?'
const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement

function shown(props: Partial<Parameters<typeof InlineConfirm>[0]> = {}) {
  const onConfirm = vi.fn()
  const view = render(<InlineConfirm label="Change it" question={QUESTION} onConfirm={onConfirm} {...props} />)
  return { onConfirm, rerender: (next: Partial<Parameters<typeof InlineConfirm>[0]>) =>
    view.rerender(<InlineConfirm label="Change it" question={QUESTION} onConfirm={onConfirm} {...props} {...next} />) }
}

describe('the in-app ask (ticket 441)', () => {
  it('is three plain buttons, never a form submit', async () => {
    shown()
    expect(button('Change it').type).toBe('button')
    await userEvent.click(button('Change it'))
    expect(button('Change it').type).toBe('button')
    expect(button('Cancel').type).toBe('button')
  })

  it('takes its own confirm and cancel labels', async () => {
    const { onConfirm } = shown({ confirmLabel: 'Do it anyway', cancelLabel: 'Keep it' })
    await userEvent.click(button('Change it'))
    expect(document.activeElement).toBe(button('Keep it'))
    await userEvent.click(button('Do it anyway'))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('on Escape closes and goes no further, so an open chat or the phone bar stays open', async () => {
    const heard = vi.fn()
    document.addEventListener('keydown', heard)
    try {
      shown()
      await userEvent.click(button('Change it'))
      await userEvent.keyboard('{Escape}')
      expect(screen.queryByRole('alert')).toBeNull()
      expect(heard).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener('keydown', heard)
    }
  })

  it('holds its confirm button while confirmDisabled', async () => {
    const { onConfirm, rerender } = shown()
    await userEvent.click(button('Change it'))
    rerender({ confirmDisabled: true })
    expect(button('Change it').disabled).toBe(true)
    await userEvent.click(button('Change it'))
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it("can't open while its trigger is aria-disabled, which keeps it focusable", async () => {
    shown({ trigger: { 'aria-disabled': true } })
    await userEvent.click(button('Change it'))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(button('Change it').disabled).toBe(false)
  })

  it('closes, focus back on its trigger, when what it asks about changes while open', async () => {
    const { onConfirm, rerender } = shown()
    await userEvent.click(button('Change it'))
    rerender({ question: 'Change that to this?' })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(document.activeElement).toBe(button('Change it'))
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('closes when its subject changes though the question reads the same', async () => {
    const { rerender } = shown({ subject: 'one' })
    await userEvent.click(button('Change it'))
    rerender({ subject: 'two' })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('without ask, confirms in one press', async () => {
    const { onConfirm } = shown({ ask: false })
    await userEvent.click(button('Change it'))
    expect(onConfirm).toHaveBeenCalledOnce()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
