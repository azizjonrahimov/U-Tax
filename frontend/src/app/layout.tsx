import type { Metadata } from "next";
import { Doto, Geist, Geist_Mono, Instrument_Serif, Inter, JetBrains_Mono, Schibsted_Grotesk } from "next/font/google";
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

// D39/D41 F44 "Night Lime": Geist for headlines and body, Instrument Serif (normal and italic) for the one
// accent word, Geist Mono for microcopy; the theater keeps JetBrains Mono. Variable fonts, all weights.
const geist = Geist({ subsets: ["latin"], variable: "--font-geist", display: "swap" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });
const instrumentSerif = Instrument_Serif({ subsets: ["latin"], weight: "400", style: ["normal", "italic"], variable: "--font-instrument-serif", display: "swap" });
// D42 P1: Doto, the dot-matrix display face of the immersive landing (`.dot-display`, the card textures).
const doto = Doto({ subsets: ["latin"], weight: ["700", "800"], variable: "--font-doto", display: "swap" });

export const metadata: Metadata = {
  title: "U-Tax · Your Delaware franchise tax, checked free",
  description: "An accounting firm run by AI agents. A free Delaware franchise-tax checkup, with every step on the record.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} ${schibsted.variable} ${geist.variable} ${geistMono.variable} ${instrumentSerif.variable} ${doto.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
