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
  /** Focus the button when it appears: the profile dropdown after a switch, which opens the app afresh. */
  focusOnMount?: boolean
  /** The menu's items, given a function that closes the menu (and puts focus back on the button). */
  children: (close: () => void) => ReactNode
}

/**
 * A menu button (#222, the top bar's profile dropdown and user menu): a click, Enter, Space or Arrow Down opens it,
 * the keyboard on its first item; the arrows move between its items, round again, Home and End to the first and last
 * (#615); Esc, Tab or a click outside closes
 * it, Esc back on the button. Its items are `MenuItem`s, or anything with role menuitem or menuitemradio.
 */
export default function Menu({ label, button, className, buttonClassName, focusOnMount = false, children }:
  MenuProps) {
  const [open, setOpen] = useState(false)
  const [focusFirst, setFocusFirst] = useState(false)
  const [returns, setReturns] = useState(0) // each close from inside counts one: focus goes back to the button
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const close = (backToButton = true) => {
    setOpen(false)
    if (backToButton) setReturns((n) => n + 1)
  }
  useEffect(() => {
    if (returns > 0 || focusOnMount) trigger.current?.focus()
  }, [returns, focusOnMount])
  useEffect(() => {
    if (!open) return
    const items = () => [...(root.current?.querySelectorAll<HTMLElement>(ITEMS) ?? [])]
    if (focusFirst) items()[0]?.focus()
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    // On the document, before anything else hears it, while open: wherever focus is (a click leaves it on the body in
    // some browsers), Esc closes this menu and goes no further, so an open chat stays open; the arrows move.
    const keys = (e: globalThis.KeyboardEvent) => {
      const all = items()
      const at = all.indexOf(document.activeElement as HTMLElement)
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
        setReturns((n) => n + 1)
      } else if (e.key === 'Tab') {
        setOpen(false)
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const step = e.key === 'ArrowDown' ? 1 : -1
        all[at === -1 ? (step === 1 ? 0 : all.length - 1) : (at + step + all.length) % all.length]?.focus()
      } else if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault()
        all[e.key === 'Home' ? 0 : all.length - 1]?.focus()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keys, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keys, true)
    }
  }, [open, focusFirst])
  const onButtonKey = (e: KeyboardEvent) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      setFocusFirst(true)
      setOpen(true)
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
        <div id={id} role="menu" aria-label={label} className="menu-popup">
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
