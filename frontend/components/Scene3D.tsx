"use client";

import { useEffect, useRef } from "react";
import type { SceneHandle, SceneVariant } from "@/components/scene/moneyScene";

/**
 * Mounts the three.js money scene (components/scene/moneyScene.ts) into a decorative box.
 *
 * three.js is ~150 KB gzipped, so it is imported only when the box first scrolls into view, never
 * on the server, and never on pages that don't render this. The loop runs only while the box is on
 * screen and the tab is visible. With reduced motion the scene renders one still frame. If WebGL
 * is unavailable the box simply stays empty and the CSS backdrop behind it carries the design.
 */
export default function Scene3D({ variant, className = "" }: { variant: SceneVariant; className?: string }) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let handle: SceneHandle | null = null;
    let loading = false;
    let visible = false;
    let cancelled = false;

    const sync = () => {
      if (!handle) return;
      if (visible && !document.hidden) handle.start();
      else handle.stop();
    };

    const load = () => {
      loading = true;
      import("@/components/scene/moneyScene")
        .then(({ mountMoneyScene }) => {
          if (cancelled) return;
          const css = getComputedStyle(document.documentElement);
          handle = mountMoneyScene(el, {
            variant,
            accent: css.getPropertyValue("--accent").trim() || "#2a78d6",
            reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
            pointer: matchMedia("(pointer: fine)").matches,
          });
          if (!handle) console.warn("3D scene unavailable: WebGL could not start");
          sync();
        })
        // a failed chunk load or scene error leaves the 2D backdrop, which is fine — but say why
        .catch((err) => console.warn("3D scene unavailable:", err));
    };

    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (visible && !loading) load();
      sync();
    }, { rootMargin: "120px" });
    io.observe(el);
    document.addEventListener("visibilitychange", sync);

    return () => {
      cancelled = true;
      io.disconnect();
      document.removeEventListener("visibilitychange", sync);
      handle?.dispose();
    };
  }, [variant]);

  return <div ref={host} className={`scene3d ${className}`} aria-hidden />;
}
