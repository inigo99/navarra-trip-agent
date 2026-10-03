export const API = "/api"; // proxy de Next a la API (next.config.ts)

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
  if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
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
  const r = await fetch(API + ruta);
  return r.ok ? r.json() : null;
}

/** Todos los lugares del plan por id, para el panel de detalle. */
export function lugares(plan: Plan): Record<string, Lugar> {
  const todos = [
    ...plan.alojamientos,
    ...plan.dias.flatMap((d) => [
      ...d.paradas,
      ...(d.comida ? [d.comida] : []),
      ...(d.cena ? [d.cena] : []),
      ...(d.ronda ?? []),
    ]),
  ];
  return Object.fromEntries(todos.map((l) => [l.id, l]));
}
