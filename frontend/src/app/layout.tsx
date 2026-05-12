import "./globals.css";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Inter, JetBrains_Mono, Fraunces } from "next/font/google";
import { ThemeBootScript } from "@/components/ui/ThemeBoot";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

const display = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
  axes: ["opsz"],
});

const SITE_DESCRIPTION =
  "Asymmetric Yield Via Intelligent Decisions. Swarms of LLM agents " +
  "read every filing, trace supply-chain shocks through a live causal " +
  "graph, and route real orders to Interactive Brokers — end to end, " +
  "no humans on the trade path.";

export const metadata: Metadata = {
  title: {
    default: "AYVID — An AI-Native Hedge Fund",
    template: "%s · AYVID",
  },
  description: SITE_DESCRIPTION,
  metadataBase: new URL("https://ayvid.ai"),
  icons: {
    // The Golden-Gnomon mark with the single gold apex — readable down
    // to 16px tab favicons.
    icon: "/brand/favicon.svg",
    shortcut: "/brand/favicon.svg",
    apple: "/brand/mark-dark.svg",
  },
  openGraph: {
    title: "AYVID — An AI-Native Hedge Fund",
    description: SITE_DESCRIPTION,
    images: [{ url: "/brand/og-image.svg", width: 1200, height: 630 }],
    type: "website",
    siteName: "AYVID",
  },
  twitter: {
    card: "summary_large_image",
    title: "AYVID",
    description: SITE_DESCRIPTION,
    images: ["/brand/og-image.svg"],
  },
};

// Tab-bar / safe-area tint.  Light tint matches the warm cream paper,
// dark tint matches the warm graphite — both are token-aligned so the
// chrome around the page never looks "off-register".
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F4EFE6" },
    { media: "(prefers-color-scheme: dark)", color: "#0F0D0A" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${mono.variable} ${display.variable}`}
    >
      <head>
        <ThemeBootScript />
      </head>
      <body className="min-h-screen bg-app text-fg antialiased">{children}</body>
    </html>
  );
}
