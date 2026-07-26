import type { Metadata } from "next";

import "./globals.css";


export const metadata: Metadata = {
  title: "AutoFrames — сборка видео из кадров",
  description: "Сборка синхронизированного MP4 из изображений с временными метками и аудиодорожки.",
};


export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
