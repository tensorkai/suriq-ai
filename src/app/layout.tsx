import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const inter = localFont({ src: "./fonts/InterVariable.woff2", weight: "100 900", variable: "--font-inter" });

export const metadata: Metadata = {
  title: "SuriQ | Procurement review",
  description: "Review supplier quotations against purchase requirements.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return <html lang="en" className={inter.variable}><body>{children}</body></html>;
}
