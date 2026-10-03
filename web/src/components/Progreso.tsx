"use client";

import { useT } from "@/lib/i18n";

/** Pasos del grafo ya hechos (✓) y el que está en marcha. */
export default function Progreso({ pasos }: { pasos: string[] }) {
  const t = useT();
  const hechos = new Set(pasos);
  const orden = Object.keys(t.pasos);
  const actual = orden.find((p) => !hechos.has(p));
  return (
    <ol className="space-y-1 text-sm" aria-live="polite">
      {orden.map((p) => (
        <li
          key={p}
          className={
            hechos.has(p)
              ? "text-green-700 dark:text-green-400"
              : p === actual
                ? "animate-pulse font-medium"
                : "text-gray-400"
          }
        >
          {hechos.has(p) ? "✓" : "·"} {t.pasos[p]}
        </li>
      ))}
    </ol>
  );
}
