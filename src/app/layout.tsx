import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SuriQ AI | Procurement review",
  description: "Local procurement verification with linked source evidence.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return <html lang="en"><body>{children}</body></html>;
}
