import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "mint",
  description: "GMI Cloud GTM operations console",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
