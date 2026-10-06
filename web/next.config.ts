import type { NextConfig } from "next";

// Web pública (NEXT_PUBLIC_DEMO=1): exportación estática con los planes de public/ejemplos.
// En local, el navegador solo habla con la web (/api/...) y Next lo pasa a la API: sin CORS ni
// puertos cruzados. 127.0.0.1 y no localhost: en Windows, Node resuelve localhost a ::1 y
// uvicorn escucha en IPv4.
const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
const API = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = DEMO
  ? { output: "export" }
  : {
      rewrites: async () => [{ source: "/api/:ruta*", destination: `${API}/:ruta*` }],
      experimental: { proxyTimeout: 10 * 60_000 }, // un plan largo con Ollama pasa de los 30 s
    };

export default nextConfig;
