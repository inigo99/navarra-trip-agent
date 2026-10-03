"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import Detalle from "./Detalle";
import Itinerario from "./Itinerario";
import Mapa from "./Mapa";
import Progreso from "./Progreso";
import { API, type Doc, lugares, obtener, sse } from "@/lib/api";
import { useT } from "@/lib/i18n";

const boton = "rounded-lg border px-3 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-800";

export default function Vista({ id }: { id: string }) {
  const t = useT();
  const router = useRouter();
  const [doc, setDoc] = useState<Doc | null | undefined>(undefined);
  const [abierto, setAbierto] = useState<string | null>(null);
  const [quitar, setQuitar] = useState<Set<string>>(new Set());
  const [cambio, setCambio] = useState("");
  const [pasos, setPasos] = useState<string[] | null>(null);
  const [aviso, setAviso] = useState("");

  useEffect(() => {
    obtener<Doc>(`/plan/${id}`).then(setDoc);
  }, [id]);
  const porId = useMemo(() => (doc ? lugares(doc.plan) : {}), [doc]);

  if (doc === undefined) return <p className="p-6">{t.cargando}</p>;
  if (doc === null) return <p className="p-6">{t.noEncontrado}</p>;

  const alternar = (lid: string) =>
    setQuitar((q) => {
      const n = new Set(q);
      if (!n.delete(lid)) n.add(lid);
      return n;
    });

  async function ajustar() {
    setPasos([]);
    setAviso("");
    try {
      await sse(`/plan/${id}/ajustar`, { cambio, quitar: [...quitar] }, (e) => {
        if (e.paso) setPasos((p) => [...(p ?? []), e.paso!]);
        else if (e.id) router.push(`/plan/${e.id}`);
        else {
          setAviso(e.pregunta ?? e.error ?? "");
          setPasos(null);
        }
      });
    } catch (e) {
      setAviso(String(e));
      setPasos(null);
    }
  }

  return (
    <main className="mx-auto w-full max-w-7xl p-4 md:p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold">{doc.peticion}</h1>
          {doc.origen && (
            <p className="text-sm text-gray-500">
              {t.ajustadoDe}{" "}
              <Link href={`/plan/${doc.origen}`} className="underline">
                {doc.origen}
              </Link>
            </p>
          )}
        </div>
        <nav className="flex flex-wrap gap-2 print:hidden">
          <a href={`${API}/plan/${id}/gpx`} className={boton}>
            {t.gpx}
          </a>
          <button onClick={() => window.print()} className={boton}>
            {t.pdf}
          </button>
          <button
            onClick={() => navigator.clipboard.writeText(location.href).then(() => setAviso(t.copiado))}
            className={boton}
          >
            {t.compartir}
          </button>
          <Link href="/" className={boton}>
            {t.nuevo}
          </Link>
        </nav>
      </header>

      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        <section className="order-2 lg:order-1">
          <Itinerario
            texto={doc.texto}
            quitar={quitar}
            onDetalle={setAbierto}
            onQuitar={alternar}
            tQuitar={t.quitar}
          />
        </section>
        <section className="order-1 h-80 lg:sticky lg:top-4 lg:order-2 lg:h-[calc(100vh-2rem)] print:h-96">
          <Mapa plan={doc.plan} onSeleccion={setAbierto} />
        </section>
      </div>

      <section className="mt-8 rounded-lg border p-4 print:hidden">
        <h2 className="font-bold">{t.ajustar}</h2>
        <textarea
          value={cambio}
          onChange={(e) => setCambio(e.target.value)}
          placeholder={t.cambio}
          maxLength={300}
          rows={2}
          className="mt-2 w-full rounded-lg border p-2"
        />
        {quitar.size > 0 && (
          <p className="text-sm text-gray-500">
            {t.quitados}: {[...quitar].map((q) => porId[q]?.nombre ?? q).join(", ")}
          </p>
        )}
        <button
          onClick={ajustar}
          disabled={pasos !== null || (!cambio.trim() && !quitar.size)}
          className="mt-2 rounded-lg bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-50"
        >
          {t.rehacer}
        </button>
        {pasos && (
          <div className="mt-3">
            <Progreso pasos={pasos} />
          </div>
        )}
      </section>
      {aviso && (
        <p role="status" className="mt-3 text-sm">
          {aviso}
        </p>
      )}

      {abierto && porId[abierto] && <Detalle lugar={porId[abierto]} onCerrar={() => setAbierto(null)} />}
    </main>
  );
}
