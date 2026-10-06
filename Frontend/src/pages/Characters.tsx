import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { CharacterSummary } from "../api/types";
import { Empty, ErrorNote } from "../components/bits";

export default function Characters() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["characters"], queryFn: () => api.get<CharacterSummary[]>("/api/characters") });
  const sync = useMutation({
    mutationFn: () => api.post<{ slug: string; changed: boolean }[]>("/api/characters/sync"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["characters"] }),
  });
  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex flex-wrap items-center gap-3">
        <h1>Characters</h1>
        <button className="btn ml-auto" onClick={() => sync.mutate()} disabled={sync.isPending}>
          {sync.isPending ? "Reading folders…" : "Sync from folders"}</button>
      </div>
      {sync.data && <p className="mt-2 text-sm text-graphite">
        {sync.data.filter((r) => r.changed).length === 0 ? "Everything was already up to date." : `Updated ${sync.data.filter((r) => r.changed).map((r) => r.slug).join(", ")}.`}</p>}
      <ErrorNote error={q.error ?? sync.error} />
      {q.data?.length === 0 && <div className="mt-6"><Empty title="No characters yet">Each character is a folder with a profile.md and a canonical image. Add one, then sync.</Empty></div>}
      <ul className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {q.data?.map((c) => (
          <li key={c.slug}>
            <Link to={`/characters/${c.slug}`} className="print block rounded-sm p-2 pb-4 hover:-rotate-[0.6deg] motion-reduce:hover:rotate-0">
              {c.canonical_image_url
                ? <img src={c.canonical_image_url} alt={c.name} className="aspect-[4/5] w-full object-cover" />
                : <div className="grid aspect-[4/5] place-items-center bg-table text-graphite">No image</div>}
              <p className="mt-3 px-1 text-lg font-bold" style={{ fontVariationSettings: '"wdth" 125' }}>{c.name}</p>
              <p className="num px-1 text-sm text-graphite">
                {c.niche_open ? "Niche still open" : "Niche set"}, {c.runs} {c.runs === 1 ? "run" : "runs"}, profile version {c.version}</p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
