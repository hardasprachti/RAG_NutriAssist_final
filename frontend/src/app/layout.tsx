import type { Metadata, Viewport } from "next";
import { Nunito_Sans, Poppins } from "next/font/google";
import "./globals.css";

const nunito = Nunito_Sans({
  variable: "--font-nunito",
  subsets: ["latin"],
});

const poppins = Poppins({
  variable: "--font-poppins",
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
});

export const metadata: Metadata = {
  title: "NutriAI Assistant",
  description:
    "Answers nutrition questions from official guidance documents, with citations.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Lets the layout use env(safe-area-inset-*) on notched iPhones.
  viewportFit: "cover",
  themeColor: "#a61c4c",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${nunito.variable} ${poppins.variable}`}>
      <body>{children}</body>
    </html>
  );
}
