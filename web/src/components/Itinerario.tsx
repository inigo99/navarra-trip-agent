"use client";

import { Fragment, type ReactNode } from "react";
import { COLORES } from "./Mapa";

type Props = {
  texto: string;
  quitar: Set<string>;
  onDetalle: (id: string) => void;
  onQuitar: (id: string) => void;
  tQuitar: string;
};

/** El itinerario ya viene compuesto en markdown por el backend (horas por código, frases del
 * LLM); aquí solo se pinta: "## Día", "- línea", "> aviso", "_nota_" y "**negrita** [id]". */
export default function Itinerario({ texto, quitar, onDetalle, onQuitar, tQuitar }: Props) {
  let dia = 0;
  let lista: ReactNode[] = [];
  const bloques: ReactNode[] = [];
  const cerrarLista = () => {
    if (lista.length)
      bloques.push(
        <ul key={bloques.length} className="space-y-2">
          {lista}
        </ul>,
      );
    lista = [];
  };

  const enLinea = (s: string) =>
    s.split(/(\*\*[^*]+\*\*(?: \[[a-z]+:[\w.-]+\])?)/).map((t, i) => {
      const m = t.match(/^\*\*([^*]+)\*\*(?: \[([a-z]+:[\w.-]+)\])?$/);
      if (!m) return <Fragment key={i}>{t}</Fragment>;
      const [, negrita, id] = m;
      if (!id) return <strong key={i}>{negrita}</strong>;
      const fuera = quitar.has(id);
      return (
        <span key={i} className={fuera ? "line-through opacity-50" : ""}>
          <button
            onClick={() => onDetalle(id)}
            className="font-semibold underline decoration-dotted hover:text-blue-600"
          >
            {negrita}
          </button>
          {!id.startsWith("aloj:") && ( // los alojamientos no se replanifican
            <button
              onClick={() => onQuitar(id)}
              title={tQuitar}
              aria-label={`${tQuitar} ${negrita}`}
              className="ml-1 text-xs text-gray-400 hover:text-red-600 print:hidden"
            >
              ✕
            </button>
          )}
        </span>
      );
    });

  for (const linea of texto.split("\n")) {
    if (linea.startsWith("## ")) {
      cerrarLista();
      const esDia = /\d/.test(linea);
      if (esDia) dia++;
      bloques.push(
        <h2
          key={bloques.length}
          className="mt-6 border-l-4 pl-2 text-lg font-bold"
          style={{ borderColor: esDia ? COLORES[(dia - 1) % COLORES.length] : "#6b7280" }}
        >
          {linea.slice(3)}
        </h2>,
      );
    } else if (linea.startsWith("- ")) {
      lista.push(<li key={lista.length}>{enLinea(linea.slice(2))}</li>);
    } else if (linea.startsWith("> ")) {
      cerrarLista();
      bloques.push(
        <p
          key={bloques.length}
          className="mt-4 rounded bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100"
        >
          {linea.slice(2)}
        </p>,
      );
    } else if (linea.startsWith("_")) {
      cerrarLista();
      bloques.push(
        <p key={bloques.length} className="mt-4 text-sm italic text-gray-500">
          {linea.replaceAll("_", "")}
        </p>,
      );
    }
  }
  cerrarLista();
  return <div>{bloques}</div>;
}
