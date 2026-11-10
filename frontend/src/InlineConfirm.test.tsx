import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
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

/** #527: a double-click on the confirm collapses the ask, and its second click lands on whatever moves up
 * under the pointer. After a pointer press, the next pointer click within 500ms presses nothing. */
describe('the second click of a double-click (ticket 527)', () => {
  function withOther() {
    const onConfirm = vi.fn()
    const other = vi.fn()
    render(
      <>
        <InlineConfirm label="Change it" question={QUESTION} onConfirm={onConfirm} />
        <button type="button" onClick={other}>Close round</button>
      </>,
    )
    return { onConfirm, other }
  }
  const opened = (detail = 1) => {
    fireEvent.click(button('Change it'), { detail })
    expect(screen.getByRole('alert')).toBeTruthy()
  }
  const confirmButton = () =>
    within(document.querySelector('.inline-confirm') as HTMLElement).getByRole('button', { name: 'Change it' })
  const confirm = (detail: number) => fireEvent.click(confirmButton(), { detail })

  afterEach(() => { vi.useRealTimers() })

  it('presses nothing within 500ms of a pointer confirm, wherever it lands', () => {
    vi.useFakeTimers()
    const { onConfirm, other } = withOther()
    opened()
    confirm(1)
    expect(onConfirm).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(499)
    fireEvent.click(button('Close round'), { detail: 2 })
    expect(other).not.toHaveBeenCalled()
  })

  it('swallows one click only: the next one works', () => {
    vi.useFakeTimers()
    const { other } = withOther()
    opened()
    confirm(1)
    fireEvent.click(button('Close round'), { detail: 2 })
    fireEvent.click(button('Close round'), { detail: 1 })
    expect(other).toHaveBeenCalledOnce()
  })

  it('swallows a second click that lands on its own trigger, so the ask stays closed', () => {
    vi.useFakeTimers()
    const { onConfirm } = withOther()
    opened()
    confirm(1)
    fireEvent.click(button('Change it'), { detail: 2 })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('lets a click 500ms or more after the confirm through', () => {
    vi.useFakeTimers()
    const { other } = withOther()
    opened()
    confirm(1)
    vi.advanceTimersByTime(500)
    fireEvent.click(button('Close round'), { detail: 1 })
    expect(other).toHaveBeenCalledOnce()
  })

  it('sets no guard on a keyboard confirm (detail 0)', () => {
    const { onConfirm, other } = withOther()
    opened(0)
    confirm(0)
    expect(onConfirm).toHaveBeenCalledOnce()
    fireEvent.click(button('Close round'), { detail: 0 })
    expect(other).toHaveBeenCalledOnce()
  })

  it('never swallows a keyboard click, even inside a pointer guard', () => {
    vi.useFakeTimers()
    const { other } = withOther()
    opened()
    confirm(1)
    fireEvent.click(button('Close round'), { detail: 0 })
    expect(other).toHaveBeenCalledOnce()
  })

  it('leaves no guard behind when it unmounts within the 500ms', () => {
    vi.useFakeTimers()
    const other = vi.fn()
    const view = render(<InlineConfirm label="Change it" question={QUESTION} onConfirm={vi.fn()} />)
    opened()
    confirm(1)
    view.rerender(<button type="button" onClick={other}>Close round</button>)
    fireEvent.click(button('Close round'), { detail: 2 })
    expect(other).toHaveBeenCalledOnce()
  })

  it('still confirms once on a single click, closes, and puts focus back on the trigger', async () => {
    const { onConfirm, other } = withOther()
    await userEvent.click(button('Change it'))
    await userEvent.click(confirmButton())
    expect(onConfirm).toHaveBeenCalledOnce()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(document.activeElement).toBe(button('Change it'))
    expect(other).not.toHaveBeenCalled()
  })
})
