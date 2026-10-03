import Vista from "@/components/Vista";

export default async function PaginaPlan({ params }: PageProps<"/plan/[id]">) {
  const { id } = await params;
  return <Vista id={id} />;
}
