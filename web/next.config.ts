import type { NextConfig } from "next";

// El navegador solo habla con la web (/api/...) y Next lo pasa a la API: sin CORS ni puertos
// cruzados. 127.0.0.1 y no localhost: en Windows, Node resuelve localhost a ::1 y uvicorn
// escucha en IPv4.
const API = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  rewrites: async () => [{ source: "/api/:ruta*", destination: `${API}/:ruta*` }],
  experimental: { proxyTimeout: 10 * 60_000 }, // un plan largo con Ollama pasa de los 30 s por defecto
};

export default nextConfig;
