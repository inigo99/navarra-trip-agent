// la API en Vercel (otro proyecto, con CORS); en local, el proxy de Next (next.config.ts)
export const API = process.env.NEXT_PUBLIC_API_URL || "/api";
// web pública estática: planes de ejemplo de public/ejemplos (navarra-demo), sin API
export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
// los planes de la API tienen ids de 8 caracteres; los de ejemplo, nombres ("estella-romanico")
export const esEjemplo = (id: string) => DEMO || !/^[A-Za-z0-9_-]{8}$/.test(id);
export const gpx = (id: string) => (esEjemplo(id) ? `/ejemplos/${id}.gpx` : `${API}/plan/${id}/gpx`);

export type Lugar = {
  id: string;
  nombre: string;
  lat: number;
  lon: number;
  [k: string]: unknown;
};
export type Dia = {
  dia: number;
  paradas: Lugar[];
  comida?: Lugar | null;
  cena?: Lugar | null;
  ronda?: Lugar[] | null;
  geometria?: { type: string; coordinates: [number, number][] } | null;
};
export type Plan = {
  requisitos: { idioma: string; dias: number };
  base: Lugar;
  dias: Dia[];
  alojamientos: Lugar[];
  opciones?: Lugar[]; // peticiones de opciones ("bares de pintxos en…"): sin días
  excluidos?: string[];
  geojson: GeoJSON.FeatureCollection;
};
export type Doc = { id: string; origen: string | null; peticion: string; plan: Plan; texto: string };
export type Evento = { paso?: string; pregunta?: string; error?: string } & Partial<Doc>;

/** POST que devuelve eventos SSE; onEvento por cada `data: {...}`. */
export async function sse(ruta: string, cuerpo: object, onEvento: (e: Evento) => void) {
  const r = await fetch(API + ruta, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cuerpo),
  });
  if (!r.ok || !r.body) {
    // 429 con detail "cupo": el total del día; la web lo explica en su idioma
    const detalle = await r.json().then((j) => j.detail, () => "");
    throw new Error(typeof detalle === "string" && detalle ? detalle : `HTTP ${r.status}`);
  }
  const lector = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let resto = "";
  for (;;) {
    const { value, done } = await lector.read();
    if (done) break;
    const partes = (resto + value).split("\n\n");
    resto = partes.pop() ?? "";
    for (const p of partes) if (p.startsWith("data: ")) onEvento(JSON.parse(p.slice(6)));
  }
}

export async function obtener<T>(ruta: string): Promise<T | null> {
  const [, tipo, id] = ruta.split("/");
  if (DEMO || (tipo === "plan" && esEjemplo(id))) return demo<T>(ruta);
  const r = await fetch(API + ruta);
  return r.ok ? r.json() : null;
}

async function demo<T>(ruta: string): Promise<T | null> {
  const [, tipo, id] = ruta.split("/"); // /plan/{id} o /recurso/{id}
  const r = await fetch(tipo === "plan" ? `/ejemplos/${id}.json` : "/ejemplos/recursos.json");
  if (!r.ok) return null;
  const datos = await r.json();
  return tipo === "plan" ? datos : (datos[decodeURIComponent(id)] ?? null);
}

/** Todos los lugares del plan por id, para el panel de detalle. */
export function lugares(plan: Plan): Record<string, Lugar> {
  const todos = [
    ...plan.alojamientos,
    ...(plan.opciones ?? []),
    ...plan.dias.flatMap((d) => [
      ...d.paradas,
      ...(d.comida ? [d.comida] : []),
      ...(d.cena ? [d.cena] : []),
      ...(d.ronda ?? []),
    ]),
  ];
  return Object.fromEntries(todos.map((l) => [l.id, l]));
}
