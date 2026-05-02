import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CGAE Live Economy",
  description: "Comprehension-Gated Agent Economy — Solana Devnet",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-black text-zinc-100 antialiased min-h-screen selection:bg-purple-500/30">{children}</body>
    </html>
  );
}
