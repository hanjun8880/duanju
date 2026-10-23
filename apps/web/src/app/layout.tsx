import type { Metadata } from "next";
import { Noto_Serif_SC } from "next/font/google";
import "./globals.css";

const serif = Noto_Serif_SC({ weight: ["400", "600", "900"], subsets: ["latin"], variable: "--font-serif", display: "swap" });

export const metadata: Metadata = {
  title: "长卷 · 短剧导演台",
  description: "连续剧剧本、定妆、分镜与渲染预算的一体化导演工作台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${serif.variable} antialiased`}>{children}</body>
    </html>
  );
}
