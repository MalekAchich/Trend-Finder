import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { CharacterDetail } from "../api/types";
import { ErrorNote } from "../components/bits";

function TasteProfile({ c }: { c: CharacterDetail }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body_md: string) => api.put<{ version: number }>(`/api/characters/${c.slug}/taste-profile`, { body_md }),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ["character", c.slug] }); },
  });
  const tp = c.taste_profile;
  return (
    <section>
      <div className="flex items-baseline gap-3">
        <h2>Taste profile</h2>
        {tp && <span className="num text-sm text-graphite">version {tp.version}, written by {tp.author === "owner" ? "you" : "the agents"}</span>}
        {draft === null && <button className="btn ml-auto" onClick={() => setDraft(tp?.body_md ?? "")}>Edit</button>}
      </div>
      <p className="mt-1 text-sm text-graphite">What the agents learned from your ratings. Every search reads it.</p>
      {draft === null
        ? <div className="mt-3 whitespace-pre-line rounded-lg border border-rule bg-paper p-4 text-[0.95rem]">
            {tp?.body_md ?? "Empty until you rate your first run."}</div>
        : <>
            <textarea className="field mt-3 h-72 font-normal" maxLength={8000} value={draft} onChange={(e) => setDraft(e.target.value)}
              aria-label="Taste profile" />
            <div className="mt-2 flex gap-2">
              <button className="btn btn-primary" disabled={save.isPending || !draft.trim()} onClick={() => save.mutate(draft)}>Save as new version</button>
              <button className="btn" onClick={() => setDraft(null)}>Cancel</button>
            </div>
          </>}
      <ErrorNote error={save.error} />
    </section>
  );
}

function Seeds({ c }: { c: CharacterDetail }) {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const add = useMutation({
    mutationFn: () => api.post(`/api/characters/${c.slug}/seeds`, { url: url.trim() }),
    onSuccess: () => { setUrl(""); qc.invalidateQueries({ queryKey: ["character", c.slug] }); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); add.mutate(); };
  return (
    <section>
      <h2>Example videos</h2>
      <p className="mt-1 text-sm text-graphite">Videos you'd love this character to recreate. The agents study them for patterns.</p>
      <form onSubmit={submit} className="mt-3 flex gap-2">
        <input className="field" type="url" placeholder="Paste a TikTok, Instagram or YouTube Shorts link" value={url}
          onChange={(e) => setUrl(e.target.value)} required minLength={10} aria-label="Video link" />
        <button className="btn shrink-0" disabled={add.isPending}>Add video</button>
      </form>
      <ErrorNote error={add.error} />
      <ul className="mt-3 space-y-1 text-sm">
        {c.seeds.map((s) => <li key={s.url} className="truncate"><a href={s.url} target="_blank" rel="noreferrer" className="underline">{s.url}</a></li>)}
      </ul>
    </section>
  );
}

export default function CharacterPage() {
  const { slug } = useParams();
  const q = useQuery({ queryKey: ["character", slug], queryFn: () => api.get<CharacterDetail>(`/api/characters/${slug}`) });
  if (q.error) return <ErrorNote error={q.error} />;
  const c = q.data;
  if (!c) return <p className="text-graphite">Loading…</p>;
  const mean = (d: { alpha: number; beta: number }) => (d.alpha + 1) / (d.alpha + d.beta + 2);
  const directions = [...c.directions].sort((a, b) => mean(b) - mean(a));
  return (
    <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[18rem_1fr]">
      <aside>
        {c.canonical_image_url && <img src={c.canonical_image_url} alt={c.name} className="print w-full rounded-sm p-2" />}
        <h1 className="mt-4">{c.name}</h1>
        <p className="text-sm text-graphite">{c.niche_open ? "The agents are still looking for the niche." : "Niche set in the profile."}</p>
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer font-semibold">What the agents are told</summary>
          <p className="mt-2 whitespace-pre-line text-graphite">{c.brief}</p>
        </details>
      </aside>
      <div className="min-w-0 space-y-10">
        <TasteProfile c={c} />
        <section>
          <h2>Directions tried</h2>
          <p className="mt-1 text-sm text-graphite">Ideas the agents have explored. The bar shows how often you selected what they found.</p>
          {directions.length === 0 && <p className="mt-3 text-graphite">None yet.</p>}
          <ul className="mt-3 divide-y divide-rule">
            {directions.map((d) => {
              const rate = mean(d);
              const rated = d.alpha + d.beta;
              return (
                <li key={d.key} className="grid grid-cols-[1fr_8rem] items-center gap-4 py-2">
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{d.label}</p>
                    {d.niche && <p className="truncate text-sm text-graphite">{d.niche}</p>}
                  </div>
                  <div title={`${rated} rated`}>
                    <div className="h-1.5 rounded-full bg-rule"><div className="h-1.5 rounded-full bg-grease" style={{ width: `${rate * 100}%` }} /></div>
                    <p className="num mt-1 text-right text-xs text-graphite">{rated > 0 ? `${rated} rated` : "not rated yet"}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
        <Seeds c={c} />
      </div>
    </div>
  );
}
