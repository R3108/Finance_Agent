import type { Metadata, Viewport } from "next";
import AppShell from "@/components/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ledgerly — AI personal finance",
  description: "Track every rupee automatically, with numbers you can trust.",
  applicationName: "Ledgerly",
  appleWebApp: { capable: true, title: "Ledgerly", statusBarStyle: "default" },
  icons: { apple: "/pwa-icon/180" },
  formatDetection: { telephone: false },   // amounts and UPI refs aren't phone numbers
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",                     // lets the tab bar sit above the home indicator (safe-area insets)
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fcfcfb" },
    { media: "(prefers-color-scheme: dark)", color: "#1a1a19" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
