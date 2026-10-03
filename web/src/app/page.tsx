"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Progreso from "@/components/Progreso";
import { sse } from "@/lib/api";
import { useT } from "@/lib/i18n";

export default function Inicio() {
  const t = useT();
  const router = useRouter();
  const [peticion, setPeticion] = useState("");
  const [pregunta, setPregunta] = useState("");
  const [respuesta, setRespuesta] = useState("");
  const [pasos, setPasos] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  async function planificar(texto: string) {
    setPasos([]);
    setError("");
    setPregunta("");
    try {
      await sse("/plan", { peticion: texto }, (e) => {
        if (e.paso) setPasos((p) => [...(p ?? []), e.paso!]);
        else if (e.pregunta) {
          setPeticion(texto);
          setPregunta(e.pregunta);
          setPasos(null);
        } else if (e.error) {
          setError(e.error);
          setPasos(null);
        } else if (e.id) router.push(`/plan/${e.id}`);
      });
    } catch (e) {
      setError(String(e));
      setPasos(null);
    }
  }

  const ocupado = pasos !== null;
  return (
    <main className="mx-auto w-full max-w-2xl p-6">
      <h1 className="text-3xl font-bold">{t.titulo}</h1>
      <p className="mt-1 text-gray-500">{t.subtitulo}</p>
      <form
        className="mt-6 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          planificar(pregunta ? `${peticion.replace(/[. ]+$/, "")}. ${respuesta}` : peticion);
        }}
      >
        <textarea
          value={peticion}
          onChange={(e) => setPeticion(e.target.value)}
          placeholder={t.ejemplo}
          rows={3}
          minLength={3}
          maxLength={500}
          required
          disabled={ocupado || !!pregunta}
          className="w-full rounded-lg border p-3"
        />
        {pregunta && (
          <label className="block">
            <span className="font-medium">{pregunta}</span>
            <input
              value={respuesta}
              onChange={(e) => setRespuesta(e.target.value)}
              required
              autoFocus
              className="mt-1 w-full rounded-lg border p-2"
            />
          </label>
        )}
        <button
          disabled={ocupado}
          className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-50"
        >
          {pregunta ? t.responder : t.planificar}
        </button>
      </form>
      {ocupado && (
        <div className="mt-6">
          <Progreso pasos={pasos} />
        </div>
      )}
      {error && <p className="mt-4 text-red-600">{error}</p>}
    </main>
  );
}
