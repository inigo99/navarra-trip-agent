"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import type { Plan } from "@/lib/api";

const ESTILO = "https://tiles.openfreemap.org/styles/liberty";
export const COLORES = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be185d"];
const color = (dia: number) => COLORES[(dia - 1) % COLORES.length];

/** Paradas (número de orden y color del día), comidas, cenas, alojamientos y la ruta de cada
 * día. Clic en un punto: onSeleccion(id) abre su detalle. */
export default function Mapa({ plan, onSeleccion }: { plan: Plan; onSeleccion: (id: string) => void }) {
  const caja = useRef<HTMLDivElement>(null);
  const seleccion = useRef(onSeleccion);
  useEffect(() => {
    seleccion.current = onSeleccion;
  });

  useEffect(() => {
    let mapa: import("maplibre-gl").Map | undefined;
    let cancelado = false;
    import("maplibre-gl").then(({ Map, LngLatBounds, NavigationControl, setWorkerUrl }) => {
      if (cancelado || !caja.current) return;
      // el bundler no copia el worker de MapLibre: lo sirve public/ (lo copia el postinstall)
      setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
      // el GeoJSON trae también las rutas (LineString): las líneas salen de dias[].geometria
      const puntos = plan.geojson.features
        .filter((f) => f.geometry.type === "Point")
        .map((f) => ({
          ...f,
          properties: { ...f.properties, color: color(Number(f.properties?.dia ?? 1)) },
        }));
      const rutas = plan.dias
        .filter((d) => d.geometria?.type === "LineString")
        .map((d) => ({
          type: "Feature" as const,
          geometry: d.geometria as GeoJSON.LineString,
          properties: { color: color(d.dia) },
        }));
      const caja_ = new LngLatBounds();
      for (const f of puntos) caja_.extend((f.geometry as GeoJSON.Point).coordinates as [number, number]);

      mapa = new Map({
        container: caja.current,
        style: ESTILO,
        bounds: caja_,
        fitBoundsOptions: { padding: 40 },
        canvasContextAttributes: { preserveDrawingBuffer: true }, // para imprimir a PDF
      });
      mapa.addControl(new NavigationControl());
      mapa.on("load", () => {
        if (!mapa) return;
        mapa.addSource("rutas", { type: "geojson", data: { type: "FeatureCollection", features: rutas } });
        mapa.addSource("puntos", { type: "geojson", data: { type: "FeatureCollection", features: puntos } });
        mapa.addLayer({
          id: "rutas",
          type: "line",
          source: "rutas",
          paint: { "line-color": ["get", "color"], "line-width": 3, "line-opacity": 0.7 },
        });
        mapa.addLayer({
          id: "puntos",
          type: "circle",
          source: "puntos",
          paint: {
            "circle-radius": ["match", ["get", "tipo"], ["parada", "opcion"], 10, "base", 8, 6],
            "circle-color": [
              "match",
              ["get", "tipo"],
              "base",
              "#111827",
              "alojamiento",
              "#6b7280",
              ["get", "color"],
            ],
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 2,
          },
        });
        mapa.addLayer({
          id: "orden",
          type: "symbol",
          source: "puntos",
          filter: ["in", ["get", "tipo"], ["literal", ["parada", "opcion"]]],
          layout: {
            "text-field": ["to-string", ["get", "orden"]],
            "text-size": 11,
            "text-allow-overlap": true,
          },
          paint: { "text-color": "#fff" },
        });
        mapa.on("click", "puntos", (e) => {
          const id = e.features?.[0]?.properties?.id;
          if (id) seleccion.current(id);
        });
        mapa.on("mouseenter", "puntos", () => mapa && (mapa.getCanvas().style.cursor = "pointer"));
        mapa.on("mouseleave", "puntos", () => mapa && (mapa.getCanvas().style.cursor = ""));
      });
    });
    return () => {
      cancelado = true;
      mapa?.remove();
    };
  }, [plan]);

  return <div ref={caja} className="h-full min-h-80 w-full rounded-lg" />;
}
