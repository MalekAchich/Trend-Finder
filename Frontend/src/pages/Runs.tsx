import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { CharacterSummary, RunSummary } from "../api/types";
import { Empty, ErrorNote, StatePill } from "../components/bits";

const PLATFORMS = [
  { id: "tiktok", label: "TikTok" },
  { id: "instagram", label: "Instagram" },
  { id: "youtube", label: "YouTube Shorts" },
];

function NewRun({ characters }: { characters: CharacterSummary[] }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [slug, setSlug] = useState(characters[0]?.slug ?? "");
  const [platforms, setPlatforms] = useState(PLATFORMS.map((p) => p.id));
  const [rounds, setRounds] = useState(3);
  const [tasks, setTasks] = useState(12);
  const [target, setTarget] = useState(20);
  const [minutes, setMinutes] = useState(60);
  const start = useMutation({
    mutationFn: () => api.post<{ run_id: string }>("/api/runs", {
      slug, platforms, rounds, tasks_per_round: tasks, target_findings: target, minutes,
    }),
    onSuccess: ({ run_id }) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run_id}`);
    },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); start.mutate(); };
  const toggle = (id: string) =>
    setPlatforms((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  return (
    <form onSubmit={submit} className="print rounded-lg p-5">
      <h2>Start a run</h2>
      <p className="mt-1 text-sm text-graphite">The agents search the platforms, watch what they find and rank it for the character.</p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block text-sm font-semibold">Character
          <select className="field mt-1" value={slug} onChange={(e) => setSlug(e.target.value)} required>
            {characters.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
          </select>
        </label>
        <fieldset>
          <legend className="text-sm font-semibold">Platforms</legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {PLATFORMS.map((p) => (
              <label key={p.id} className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={platforms.includes(p.id)} onChange={() => toggle(p.id)}
                  className="accent-grease" />{p.label}
              </label>
            ))}
          </div>
        </fieldset>
        {([["Rounds", rounds, setRounds, 1, 10], ["Searches per round", tasks, setTasks, 1, 16],
          ["Trend cards wanted", target, setTarget, 1, 200], ["Time limit (minutes)", minutes, setMinutes, 1, 600]] as const)
          .map(([label, value, set, min, max]) => (
            <label key={label} className="block text-sm font-semibold">{label}
              <input type="number" className="field num mt-1" min={min} max={max} value={value}
                onChange={(e) => set(Number(e.target.value))} required />
            </label>
          ))}
      </div>
      <div className="mt-5 flex items-center gap-3">
        <button className="btn btn-primary" disabled={start.isPending || !slug || platforms.length === 0}>
          {start.isPending ? "Starting…" : "Start run"}
        </button>
        <ErrorNote error={start.error} />
      </div>
    </form>
  );
}

export default function Runs() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<RunSummary[]>("/api/runs"), refetchInterval: 10_000 });
  const chars = useQuery({ queryKey: ["characters"], queryFn: () => api.get<CharacterSummary[]>("/api/characters") });

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <h1>Runs</h1>
      {chars.data && chars.data.length > 0 && <NewRun characters={chars.data} />}
      {chars.data?.length === 0 && (
        <Empty title="No characters yet">Add a character folder with a profile.md, then <Link className="underline" to="/characters">sync characters</Link>.</Empty>
      )}
      <ErrorNote error={runs.error ?? chars.error} />
      <section>
        <h2>Recent runs</h2>
        {runs.data?.length === 0 && <p className="mt-2 text-graphite">No runs yet. Start one above.</p>}
        <ul className="mt-3 divide-y divide-rule rounded-lg border border-rule bg-paper">
          {runs.data?.map((r) => (
            <li key={r.id}>
              <Link to={r.state === "review_ready" ? `/runs/${r.id}/review` : `/runs/${r.id}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-table/60">
                <span className="font-semibold capitalize">{r.character}</span>
                <StatePill state={r.state} />
                <span className="num text-sm text-graphite">round {r.round}</span>
                {r.stop_reason && <span className="text-sm text-graphite">{r.stop_reason}</span>}
                <span className="num ml-auto text-sm text-graphite">{new Date(r.started_at).toLocaleString()}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
