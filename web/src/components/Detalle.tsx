"use client";

import { useEffect, useState } from "react";
import { obtener, type Lugar } from "@/lib/api";
import { useT } from "@/lib/i18n";

const texto = (v: unknown) => (typeof v === "string" && v ? v : null);

/** Ficha de un lugar: lo que trae el plan y, si es monumento, espacio o ruta, la ficha
 * completa de /recurso (descripción entera, imagen). Solo datos de fuente. */
export default function Detalle({ lugar, onCerrar }: { lugar: Lugar; onCerrar: () => void }) {
  const t = useT();
  const [ficha, setFicha] = useState<Lugar | null>(null);
  useEffect(() => {
    let vivo = true;
    obtener<Lugar>(`/recurso/${encodeURIComponent(lugar.id)}`).then((f) => vivo && setFicha(f));
    return () => {
      vivo = false;
    };
  }, [lugar.id]);

  const l = { ...lugar, ...ficha };
  const imagen = texto(l.imagen_url);
  const lugarDe = texto(l.municipio) ?? texto(l.localidad);
  const datos: [string, string | null][] = [
    [t.horario, texto(l.horario)],
    [t.precio, texto(l.precio)],
  ];
  return (
    <aside
      role="dialog"
      aria-label={t.detalle}
      className="fixed inset-y-0 right-0 z-10 w-full max-w-md overflow-y-auto bg-white p-5 shadow-2xl dark:bg-gray-900 print:hidden"
    >
      <button onClick={onCerrar} className="float-right text-sm text-gray-500 hover:underline">
        {t.cerrar}
      </button>
      <h2 className="pr-16 text-xl font-bold">{l.nombre}</h2>
      {lugarDe && (
        <p className="text-gray-500">
          {lugarDe}
          {texto(l.especialidad) && ` · ${l.especialidad}`}
        </p>
      )}
      {imagen && (
        // eslint-disable-next-line @next/next/no-img-element -- imágenes de Wikimedia, sin optimizar
        <img src={imagen} alt={l.nombre} className="mt-3 max-h-64 w-full rounded object-cover" />
      )}
      {texto(l.descripcion) && <p className="mt-3 whitespace-pre-line text-sm">{texto(l.descripcion)}</p>}
      <dl className="mt-3 space-y-1 text-sm">
        {datos
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k}>
              <dt className="inline font-medium">{k}: </dt>
              <dd className="inline">{v}</dd>
            </div>
          ))}
      </dl>
      <p className="mt-3 space-x-3 text-sm">
        {texto(l.web) && (
          <a
            href={texto(l.web)!}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 underline"
          >
            {t.web}
          </a>
        )}
        {texto(l.url_fuente) && (
          <a
            href={texto(l.url_fuente)!}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 underline"
          >
            {t.fuente}
          </a>
        )}
      </p>
      {texto(l.licencia) && <p className="mt-2 text-xs text-gray-400">{texto(l.licencia)}</p>}
    </aside>
  );
}
