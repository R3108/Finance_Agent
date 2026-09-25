import type { MetadataRoute } from "next";

/** Makes Ledgerly installable ("Add to Home screen"): it opens full-screen, like a native app. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Ledgerly — personal finance",
    short_name: "Ledgerly",
    description: "Track every rupee automatically, with numbers you can trust.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f6f6f3",
    theme_color: "#2a78d6",
    categories: ["finance", "productivity"],
    icons: [
      { src: "/pwa-icon/192", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/pwa-icon/512", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/pwa-icon/512?maskable=1", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "Add from bank SMS", url: "/capture" },
      { name: "Can I afford it?", url: "/afford" },
      { name: "Split a bill", url: "/splits" },
    ],
  };
}
