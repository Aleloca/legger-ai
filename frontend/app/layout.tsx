import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Sans, Literata } from "next/font/google";
import "./globals.css";

/**
 * Tipografia "editoriale italiano raffinato":
 * - Fraunces (display, optical sizing): titoli e intestazioni dei pannelli.
 * - IBM Plex Sans: chrome dell'interfaccia e chat.
 * - Literata (optical sizing): testo della norma nel pannello di lettura.
 */
const fraunces = Fraunces({
  variable: "--font-display",
  subsets: ["latin"],
  axes: ["opsz"],
});

const ibmPlexSans = IBM_Plex_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
});

const literata = Literata({
  variable: "--font-serif",
  subsets: ["latin"],
  axes: ["opsz"],
});

export const metadata: Metadata = {
  title: "Legger — ricerca normativa",
  description:
    "Ricerca e consultazione della normativa italiana vigente. Strumento informativo, non costituisce consulenza legale.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="it"
      className={`${fraunces.variable} ${ibmPlexSans.variable} ${literata.variable} h-full antialiased`}
    >
      <body className="flex h-dvh flex-col">
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        <footer className="border-t border-border px-6 py-3">
          <p className="mx-auto max-w-3xl text-center text-xs leading-relaxed text-muted-foreground">
            Strumento informativo, non costituisce consulenza legale. Fa fede
            la Gazzetta Ufficiale.
          </p>
        </footer>
      </body>
    </html>
  );
}
