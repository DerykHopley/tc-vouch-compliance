import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Menu, { MenuItem } from './Menu.tsx'

afterEach(cleanup)

function shown(onPick = vi.fn()) {
  render(
    <>
      <Menu label="Your menu" button="AB">
        {(close) => (
          <>
            <MenuItem onSelect={() => { onPick('first'); close() }}>First</MenuItem>
            <MenuItem onSelect={() => { onPick('second'); close() }}>Second</MenuItem>
          </>
        )}
      </Menu>
      <button>Elsewhere</button>
    </>,
  )
  return { onPick, button: screen.getByRole('button', { name: 'Your menu' }) }
}

describe('Menu (issue 222)', () => {
  it('opens on a click, with aria-expanded, and an item closes it', async () => {
    const { onPick, button } = shown()
    expect(button.getAttribute('aria-haspopup')).toBe('menu')
    expect(button.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('menu')).toBeNull()

    await userEvent.click(button)
    expect(button.getAttribute('aria-expanded')).toBe('true')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Second' }))

    expect(onPick).toHaveBeenCalledWith('second')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('opens from the keyboard on its first item, moves with the arrows, and Esc closes it back on the button',
    async () => {
      const { button } = shown()
      button.focus()
      await userEvent.keyboard('{Enter}')
      expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'First' }))
      await userEvent.keyboard('{ArrowDown}')
      expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Second' }))
      await userEvent.keyboard('{ArrowDown}')
      expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'First' })) // round again
      await userEvent.keyboard('{Escape}')
      expect(screen.queryByRole('menu')).toBeNull()
      expect(document.activeElement).toBe(button)
    })

  it('closes with Esc after a click opened it, focus still on its button', async () => {
    const { button } = shown()
    await userEvent.click(button)
    expect(document.activeElement).toBe(button)
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('closes on a click outside it', async () => {
    const { button } = shown()
    await userEvent.click(button)
    await userEvent.click(screen.getByRole('button', { name: 'Elsewhere' }))
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
