import type { Metadata } from "next";
import { Geist, Geist_Mono, Instrument_Serif, Inter, JetBrains_Mono, Schibsted_Grotesk } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

// D32 display face (DESIGN.md): headlines and section heads only.
const schibsted = Schibsted_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

// D39 "Night Lime" (the landing): Geist for headlines and body, one italic Instrument Serif word per
// headline, Geist Mono for microcopy. Only the weights the brief lists; the theater keeps JetBrains Mono.
// preload: false, so /office, /talk and /ceo don't fetch the landing's faces before their own first paint.
const geist = Geist({ variable: "--font-geist", subsets: ["latin"], weight: ["400", "500", "600"], display: "swap", preload: false });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"], weight: ["400", "500"], display: "swap", preload: false });
const instrumentSerif = Instrument_Serif({ variable: "--font-instrument-serif", subsets: ["latin"], weight: "400", style: "italic", display: "swap", preload: false });

export const metadata: Metadata = {
  title: "U-Tax · Your Delaware franchise tax, checked free",
  description: "An accounting firm run by AI agents. A free Delaware franchise-tax checkup, with every step on the record.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} ${schibsted.variable} ${geist.variable} ${geistMono.variable} ${instrumentSerif.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
