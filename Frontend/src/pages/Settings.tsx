import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProviderStatus, Weights } from "../api/types";
import { ErrorNote } from "../components/bits";

const KEYS: { key: keyof Weights; label: string }[] = [
  { key: "fit", label: "Fits the character" },
  { key: "feasibility", label: "Easy to recreate" },
  { key: "momentum", label: "Growing fast" },
  { key: "freshness", label: "Still fresh" },
];
const pct = (n: number) => Math.round(n * 100);

function WeightsForm() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"],
    queryFn: () => api.get<{ weights: Weights; weight_suggestion: Weights | null }>("/api/settings") });
  const [w, setW] = useState<Weights | null>(null);
  useEffect(() => { if (q.data && !w) setW(q.data.weights); }, [q.data, w]);
  const save = useMutation({
    mutationFn: (weights: Weights) => api.put("/api/settings", { weights }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  if (!w) return <ErrorNote error={q.error} />;
  const total = KEYS.reduce((a, k) => a + w[k.key], 0);
  const valid = Math.abs(total - 1) <= 0.01;
  const s = q.data?.weight_suggestion;
  return (
    <section>
      <h2>How cards are ranked</h2>
      <p className="mt-1 text-sm text-graphite">The overall score mixes these four. They must add up to 100.</p>
      <div className="mt-4 space-y-3">
        {KEYS.map(({ key, label }) => (
          <label key={key} className="grid grid-cols-[10rem_1fr_3.5rem] items-center gap-3 text-sm">
            <span className="font-semibold">{label}</span>
            <input type="range" min={0} max={100} value={pct(w[key])} className="accent-grease"
              onChange={(e) => setW({ ...w, [key]: Number(e.target.value) / 100 })} />
            <span className="num text-right">{pct(w[key])}</span>
          </label>
        ))}
      </div>
      <p className={`num mt-2 text-sm ${valid ? "text-graphite" : "text-grease"}`}>Total {pct(total)} of 100</p>
      <div className="mt-3 flex gap-2">
        <button className="btn btn-primary" disabled={!valid || save.isPending} onClick={() => save.mutate(w)}>Save weights</button>
        {save.isSuccess && <span className="self-center text-sm text-graphite">Saved. New runs use these.</span>}
      </div>
      <ErrorNote error={save.error} />
      {s && (
        <div className="mt-6 rounded-lg border border-rule bg-paper p-4">
          <h3>Suggested from your ratings</h3>
          <p className="num mt-1 text-sm">{KEYS.map((k) => `${k.label} ${pct(s[k.key])}`).join(", ")}</p>
          <button className="btn mt-3" onClick={() => setW(s)}>Use the suggestion</button>
          <p className="mt-2 text-xs text-graphite">This only fills the sliders. Save to apply it.</p>
        </div>
      )}
    </section>
  );
}

export default function Settings() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProviderStatus[]>("/api/providers"), refetchInterval: 30_000 });
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: () => api.get<Record<string, string>>("/api/platforms"), refetchInterval: 30_000 });
  return (
    <div className="mx-auto max-w-3xl space-y-12">
      <h1>Settings</h1>
      <WeightsForm />
      <section>
        <h2>AI accounts</h2>
        <p className="mt-1 text-sm text-graphite">Connect from the terminal with <code>tf login chatgpt</code> or by signing in to the Claude CLI.</p>
        <ErrorNote error={providers.error} />
        <ul className="mt-3 divide-y divide-rule rounded-lg border border-rule bg-paper">
          {providers.data?.map((p) => (
            <li key={p.provider} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <span className="font-semibold capitalize">{p.provider === "chatgpt" ? "ChatGPT" : p.provider}</span>
              <span className={`text-sm ${p.connected && p.status === "ok" ? "text-olive" : "text-grease"}`}>
                {!p.connected ? "Not connected" : p.status === "ok" ? "Ready" : p.status === "cooling" ? "At its usage limit" : "Sign-in expired"}</span>
              {p.used_percent != null && <span className="num text-sm text-graphite">{Math.round(p.used_percent)}% of the usage window used</span>}
              {p.cooling_until && <span className="num text-sm text-graphite">back at {new Date(p.cooling_until * 1000).toLocaleTimeString()}</span>}
              <span className="ml-auto truncate text-sm text-graphite">{p.account ?? p.detail}</span>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2>Platforms</h2>
        <ErrorNote error={platforms.error} />
        <ul className="mt-3 divide-y divide-rule rounded-lg border border-rule bg-paper">
          {platforms.data && Object.entries(platforms.data).map(([name, health]) => (
            <li key={name} className="flex items-center justify-between px-4 py-3">
              <span className="font-semibold capitalize">{name}</span>
              <span className={`text-sm ${health === "ok" ? "text-olive" : "text-grease"}`}>{({ ok: "Working", degraded: "Slow or flaky", unavailable: "Down for now", needs_login: "Needs a login" } as Record<string, string>)[health] ?? health}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
