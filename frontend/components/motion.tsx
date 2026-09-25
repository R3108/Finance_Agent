"use client";

/**
 * Small motion primitives shared across the app. Each one respects `prefers-reduced-motion`,
 * writes per-frame values straight to CSS variables (no React re-render per pointer move), and
 * leaves the page fully readable if it never runs. The matching styles live in globals.css under
 * "motion & effects".
 */
import { type CSSProperties, type ReactNode, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";

function subscribeReduced(cb: () => void) {
  const mq = window.matchMedia(REDUCED);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}

/** True when the visitor has asked the OS for less motion. Always false during server render. */
export function useReducedMotion() {
  return useSyncExternalStore(subscribeReduced, () => window.matchMedia(REDUCED).matches, () => false);
}

const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

/**
 * Tweens a number to its new value — from zero on first show, from the previous value on change —
 * formatted every frame with `format` (e.g. `money`). Tabular digits keep the width steady.
 */
export function CountUp({ value, format = (n) => Math.round(n).toLocaleString(), duration = 1100 }: {
  value: number; format?: (n: number) => string; duration?: number;
}) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(reduced ? value : 0);
  const current = useRef(shown);

  useEffect(() => {
    if (reduced) {
      current.current = value;
      setShown(value);
      return;
    }
    const from = current.current;
    const start = performance.now();
    let raf = requestAnimationFrame(function step(now) {
      const t = Math.min(1, (now - start) / duration);
      const v = t === 1 ? value : from + (value - from) * easeOutExpo(t);
      current.current = v;
      setShown(v);
      if (t < 1) raf = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(raf);
  }, [value, reduced, duration]);

  return <span className="num">{format(shown)}</span>;
}

/**
 * A card that leans toward the mouse in 3D, with a soft glare following the cursor. Mouse only:
 * touch and pen get the plain card, since there is no hover to lean toward.
 */
export function Tilt({ children, className = "", max = 6, style }: {
  children: ReactNode; className?: string; max?: number; style?: CSSProperties;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const frame = useRef(0);
  const reduced = useReducedMotion();

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const move = (e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || reduced || e.pointerType !== "mouse") return;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width;
    const py = (e.clientY - r.top) / r.height;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      el.style.setProperty("--rx", `${((0.5 - py) * max * 2).toFixed(2)}deg`);
      el.style.setProperty("--ry", `${((px - 0.5) * max * 2).toFixed(2)}deg`);
      el.style.setProperty("--gx", `${(px * 100).toFixed(1)}%`);
      el.style.setProperty("--gy", `${(py * 100).toFixed(1)}%`);
      el.classList.add("tilting");
    });
  };

  const leave = () => {
    cancelAnimationFrame(frame.current);
    const el = ref.current;
    if (!el) return;
    el.classList.remove("tilting");
    el.style.setProperty("--rx", "0deg");
    el.style.setProperty("--ry", "0deg");
  };

  return (
    <div ref={ref} className={`tilt ${className}`} style={style} onPointerMove={move} onPointerLeave={leave}>
      {children}
    </div>
  );
}

/**
 * A group of cards lit by a soft spotlight that follows the pointer — across the gaps too, so the
 * neighbours' borders catch the light as it passes. Children need the `spot` class.
 */
export function Spotlight({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const frame = useRef(0);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const move = (e: React.PointerEvent) => {
    if (e.pointerType !== "mouse") return;
    const { clientX, clientY } = e;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      ref.current?.querySelectorAll<HTMLElement>(".spot").forEach((card) => {
        const r = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${clientX - r.left}px`);
        card.style.setProperty("--my", `${clientY - r.top}px`);
      });
    });
  };

  return <div ref={ref} className={`spot-group ${className}`} onPointerMove={move}>{children}</div>;
}

/**
 * Fades sections in as they scroll into view. Mark elements with `data-reveal`, or a container
 * with `data-reveal-group` to stagger its children. Anything already on screen at load is left
 * alone (no flash of hidden content), and without JS or with reduced motion nothing is ever hidden.
 */
export function RevealOnScroll() {
  useLayoutEffect(() => {
    if (window.matchMedia(REDUCED).matches || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      }
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.1 });

    const fold = window.innerHeight;
    const watch = (el: HTMLElement, i = 0) => {
      if (el.getBoundingClientRect().top < fold) return;
      el.style.setProperty("--reveal-i", String(i));
      el.classList.add("reveal");
      io.observe(el);
    };
    document.querySelectorAll<HTMLElement>("[data-reveal]").forEach((el) => watch(el));
    document.querySelectorAll<HTMLElement>("[data-reveal-group]").forEach((group) => {
      Array.from(group.children).forEach((child, i) => watch(child as HTMLElement, i));
    });
    return () => io.disconnect();
  }, []);
  return null;
}
