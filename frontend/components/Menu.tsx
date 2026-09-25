"use client";

import { ReactNode, useEffect, useRef, useState } from "react";

/**
 * A small dropdown: button + popover list. Closes on Esc, on a click outside, and after choosing.
 * Items are ordinary links/buttons passed as children; `.menu-item` styles them.
 */
export default function Menu({ trigger, label, className = "btn", align = "left", up = false, children }: {
  trigger: ReactNode; label: string; className?: string; align?: "left" | "right" | "center"; up?: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    // move focus into the menu so keyboard users land on the first choice
    ref.current?.querySelector<HTMLElement>(".menu-pop .menu-item")?.focus();
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="menu" ref={ref}>
      <button type="button" className={className} aria-haspopup="menu" aria-expanded={open} aria-label={label}
              onClick={() => setOpen((o) => !o)}>
        {trigger}
      </button>
      {open && (
        <div className={`menu-pop ${align} ${up ? "up" : ""}`} role="menu" aria-label={label}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}
