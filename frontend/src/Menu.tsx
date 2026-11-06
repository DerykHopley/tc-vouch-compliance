import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from 'react'

const ITEMS = '[role="menuitem"], [role="menuitemradio"]'

type MenuProps = {
  /** The button's accessible name. */
  label: string
  /** What the button shows. */
  button: ReactNode
  className?: string
  /** The button's own class (the profile dropdown's border, the user menu's initials circle). */
  buttonClassName?: string
  /** The menu's items, given a function that closes the menu (and puts focus back on the button). */
  children: (close: () => void) => ReactNode
}

/**
 * A menu button (#222, the top bar's profile dropdown and user menu): a click, Enter, Space or Arrow Down opens it,
 * the keyboard on its first item; the arrows move between its items, round again; Esc, Tab or a click outside closes
 * it, Esc back on the button. Its items are `MenuItem`s, or anything with role menuitem or menuitemradio.
 */
export default function Menu({ label, button, className, buttonClassName, children }: MenuProps) {
  const [open, setOpen] = useState(false)
  const [focusFirst, setFocusFirst] = useState(false)
  const [returns, setReturns] = useState(0) // each close from inside counts one: focus goes back to the button
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const items = () => [...(root.current?.querySelectorAll<HTMLElement>(ITEMS) ?? [])]
  const close = (backToButton = true) => {
    setOpen(false)
    if (backToButton) setReturns((n) => n + 1)
  }
  useEffect(() => {
    if (returns > 0) trigger.current?.focus()
  }, [returns])
  useEffect(() => {
    if (!open) return
    if (focusFirst) items()[0]?.focus()
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open, focusFirst])
  const onButtonKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && open) {
      e.preventDefault()
      close() // opened by a click, focus is still on the button
    } else if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      setFocusFirst(true)
      setOpen(true)
    }
  }
  const onMenuKey = (e: KeyboardEvent) => {
    const all = items()
    const at = all.indexOf(document.activeElement as HTMLElement)
    if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if (e.key === 'Tab') {
      close(false)
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const step = e.key === 'ArrowDown' ? 1 : -1
      all[(at + step + all.length) % all.length]?.focus()
    }
  }
  return (
    <div ref={root} className={className ? `menu ${className}` : 'menu'}>
      <button ref={trigger} type="button" className={buttonClassName ? `menu-button ${buttonClassName}` : 'menu-button'}
        aria-label={label} aria-haspopup="menu"
        aria-expanded={open} aria-controls={open ? id : undefined} onKeyDown={onButtonKey}
        onClick={() => { setFocusFirst(false); setOpen((now) => !now) }}>
        {button}
      </button>
      {open && (
        <div id={id} role="menu" aria-label={label} className="menu-popup" onKeyDown={onMenuKey}>
          {children(() => close())}
        </div>
      )}
    </div>
  )
}

/** One item of a `Menu`: a button with role menuitem (`checked`: a menuitemradio, as for a choice of one). */
export function MenuItem({ onSelect, checked, children }:
  { onSelect: () => void; checked?: boolean; children: ReactNode }) {
  return (
    <button type="button" className="menu-item" role={checked === undefined ? 'menuitem' : 'menuitemradio'}
      aria-checked={checked} tabIndex={-1} onClick={onSelect}>
      {children}
    </button>
  )
}
