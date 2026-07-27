import type { Metadata } from "next";

import "./globals.css";


export const metadata: Metadata = {
  title: "AutoFrames — сборка видео из кадров",
  description: "Автоматическая синхронизация изображений с фразами и паузами в озвучке и сборка готового MP4.",
};


export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
