import { readFileSync } from "node:fs";
import Vista from "@/components/Vista";

/** En la demo estática, una página por plan de ejemplo; en local, cualquier id (la API). */
export function generateStaticParams() {
  if (process.env.NEXT_PUBLIC_DEMO !== "1") return [];
  const indice: { id: string }[] = JSON.parse(readFileSync("public/ejemplos/index.json", "utf8"));
  return indice.map(({ id }) => ({ id }));
}

export default async function PaginaPlan({ params }: PageProps<"/plan/[id]">) {
  const { id } = await params;
  return <Vista id={id} />;
}
