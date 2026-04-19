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

export const metadata: Metadata = {
  title: {
    default: "AYVID — AI-Native Hedge Fund",
    template: "%s · AYVID",
  },
  description:
    "AYVID is an AI-native hedge fund. Swarms of LLM agents read 10-Ks, earnings calls, and SEC filings, trace their consequences through a live causal graph of the global economy, and route sized, risk-limited orders into the market — end-to-end.",
  metadataBase: new URL("http://localhost:3000"),
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#faf9f5" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0e16" },
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
