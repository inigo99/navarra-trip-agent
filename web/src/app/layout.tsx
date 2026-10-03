import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Navarra Trip Agent",
  description: "Planificador de viajes por Navarra con datos abiertos",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
