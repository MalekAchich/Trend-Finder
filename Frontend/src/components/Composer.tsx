import { ChevronDown, Link2, Plus, Settings2, Sparkles, Square, Play, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Character, Platform, StartRun } from "../api/types";
import { BrandLogo } from "./brand";
import { PLATFORM_LABEL, PlatformIcon, platformOf, shortUrl } from "./ui";

const FRESHNESS = [
  { id: "day", label: "Past day" }, { id: "week", label: "Past week" },
  { id: "month", label: "Past month" }, { id: "any", label: "Any time" },
] as const;

interface Props {
  characters: Character[];
  selected: string | null;
  onSelect: (slug: string) => void;
  running: boolean;
  starting: boolean;
  error: string | null;
  onStart: (body: StartRun) => void;
  onStop: () => void;
}

export function Composer({ characters, selected, onSelect, running, starting, error, onStart, onStop }: Props) {
  const [open, setOpen] = useState<{ trending: boolean; targets: boolean }>({ trending: false, targets: false });
  const toggle = (k: "trending" | "targets") => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const [trendDraft, setTrendDraft] = useState("");
  const [trends, setTrends] = useState<string[]>([]);
  const [targets, setTargets] = useState<{ url: string; character: string }[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>(["tiktok", "instagram", "youtube"]);
  const [freshness, setFreshness] = useState<StartRun["freshness"]>("week");
  const [minutes, setMinutes] = useState(60);
  const [localError, setLocalError] = useState<string | null>(null);
  const name = characters.find((c) => c.slug === selected)?.name;

  const addTrend = () => {
    const urls = trendDraft.split(/\s+/).map((u) => u.trim()).filter(Boolean);
    const bad = urls.find((u) => !platformOf(u));
    if (bad) { setLocalError(`That isn't a TikTok, Instagram, YouTube or X link: ${bad}`); return; }
    setTrends((t) => [...new Set([...t, ...urls])]);
    setTrendDraft("");
    setLocalError(null);
  };
  const onTrendKey = (e: KeyboardEvent<HTMLInputElement>) => { if (e.key === "Enter") { e.preventDefault(); addTrend(); } };
  const togglePlatform = (p: Platform) =>
    setPlatforms((ps) => (ps.includes(p) ? ps.filter((x) => x !== p) : [...ps, p]));

  const start = () => {
    const cleanTargets = targets.filter((t) => t.url.trim());
    const bad = cleanTargets.find((t) => !platformOf(t.url));
    if (bad) { setLocalError(`That isn't a TikTok, Instagram, YouTube or X link: ${bad.url}`); setOpen((o) => ({ ...o, targets: true })); return; }
    if (!selected) return;
    setLocalError(null);
    onStart({ character: selected, platforms, freshness, minutes, trend_urls: trends,
      targets: cleanTargets.map((t) => ({ url: t.url.trim(), character: t.character || selected })) });
  };

  const shown = localError ?? error;
  return (
    <section className="wrap pb-12 pt-12 max-md:pt-8">
      <h1 className="text-[34px] font-semibold leading-tight max-md:text-[27px]">
        {name ? `What should ${name} post next?` : "Pick a character to start"}
      </h1>
      <div className="mt-9 grid gap-10 lg:grid-cols-[minmax(300px,4fr)_minmax(0,8fr)]">
        <div>
          <h2 className="mb-4 text-[13px] font-medium text-mist">Character</h2>
          {characters.length === 0 && (
            <p className="text-sm text-mist">Add a folder with images to <span className="text-snow">AI Influencers Characters</span>,
              then reload.</p>
          )}
          <div className="flex flex-wrap gap-4" role="radiogroup" aria-label="Character">
            {characters.map((c) => {
              const on = c.slug === selected;
              return (
                <button key={c.slug} role="radio" aria-checked={on} disabled={running} onClick={() => onSelect(c.slug)}
                  className="group flex flex-col items-center gap-2.5 text-[12.5px]">
                  <span className={`block overflow-hidden rounded-[10px] border border-line bg-white transition
                    ${on ? "outline-2 outline-offset-3 outline-lime" : "opacity-55 grayscale-[.7] group-hover:opacity-100 group-hover:grayscale-0"}`}>
                    {c.image_url && <img src={c.image_url} alt="" className="block h-24 w-auto max-w-[220px] object-contain" />}
                  </span>
                  <span className={on ? "text-snow" : "text-mist"}>{c.name}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="min-w-0">
          <h2 className="mb-4 text-[13px] font-medium text-mist">Optional inputs</h2>
          <div className="overflow-hidden rounded-xl border border-line bg-[color-mix(in_oklch,var(--color-panel)_45%,var(--color-night))]">
            <Section id="trending" icon={<Sparkles size={18} />} open={open.trending}
              title="Trending AI-influencer videos" subtitle="Viral AI-influencer posts the agents study for formats"
              state={trends.length ? `${trends.length} added` : "None"} onToggle={() => toggle("trending")}>
              <div className="flex gap-2">
                <input className="field" placeholder="Paste a TikTok, Reels or Shorts link and press Enter" value={trendDraft}
                  onChange={(e) => setTrendDraft(e.target.value)} onKeyDown={onTrendKey} disabled={running}
                  aria-label="Trending video link" />
                <button className="btn-ghost shrink-0" onClick={addTrend} disabled={running || !trendDraft.trim()}>Add</button>
              </div>
              {trends.length > 0 && (
                <ul className="mt-4 flex flex-wrap gap-2">
                  {trends.map((u) => (
                    <li key={u} className="chip !text-snow">
                      <PlatformIcon platform={platformOf(u)} /> <span className="max-w-[260px] truncate">{shortUrl(u)}</span>
                      <button aria-label={`Remove ${u}`} onClick={() => setTrends((t) => t.filter((x) => x !== u))}
                        disabled={running} className="-mr-1 rounded-full p-0.5 hover:bg-white/10"><X size={13} /></button>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <div className="border-t border-line" />
            <Section id="targets" icon={<Link2 size={18} />} open={open.targets}
              title="Target videos I found" subtitle="Specific videos you think one of your characters should recreate"
              state={targets.filter((t) => t.url.trim()).length ? `${targets.filter((t) => t.url.trim()).length} added` : "None"}
              onToggle={() => { toggle("targets"); if (!targets.length) setTargets([{ url: "", character: selected ?? "" }]); }}>
              <ul className="space-y-3">
                {targets.map((t, i) => (
                  <li key={i} className="flex gap-2">
                    <input className="field" placeholder="https://www.tiktok.com/@creator/video/…" value={t.url} disabled={running}
                      aria-label={`Target video ${i + 1}`}
                      onChange={(e) => setTargets((ts) => ts.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)))} />
                    <select className="field !w-40 shrink-0" value={t.character || selected || ""} disabled={running}
                      aria-label={`Character for target ${i + 1}`}
                      onChange={(e) => setTargets((ts) => ts.map((x, j) => (j === i ? { ...x, character: e.target.value } : x)))}>
                      {characters.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
                    </select>
                    <button className="btn-ghost !px-2.5" aria-label="Remove this target" disabled={running}
                      onClick={() => setTargets((ts) => ts.filter((_, j) => j !== i))}><X size={15} /></button>
                  </li>
                ))}
              </ul>
              <button className="mt-4 inline-flex items-center gap-1.5 text-[13px] text-mist hover:text-snow" disabled={running}
                onClick={() => setTargets((ts) => [...ts, { url: "", character: selected ?? "" }])}>
                <Plus size={14} /> Add another
              </button>
            </Section>
          </div>

          <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-3">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Platforms">
              {(Object.keys(PLATFORM_LABEL) as Platform[]).map((p) => {
                const on = platforms.includes(p);
                return (
                  <button key={p} className="platform-toggle" data-platform={p} aria-pressed={on} disabled={running}
                    onClick={() => togglePlatform(p)}>
                    <BrandLogo platform={p} size={15} tone={on && p !== "tiktok" ? "mono" : "brand"} />{PLATFORM_LABEL[p]}
                  </button>
                );
              })}
            </div>
            <label className="flex items-center gap-2 text-[13px] text-mist">
              Freshness
              <select className="field !h-[30px] !w-auto !rounded-full !px-3" value={freshness} disabled={running}
                onChange={(e) => setFreshness(e.target.value as StartRun["freshness"])}>
                {FRESHNESS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
              </select>
            </label>
            <Dropdown label={<><Settings2 size={13} /> Time limit {minutes} min</>} disabled={running}>
              <p className="mb-2 text-[12.5px] text-mist">Stop the run after</p>
              <div className="grid grid-cols-2 gap-1.5">
                {[15, 30, 60, 120].map((m) => (
                  <button key={m} className="chip justify-center" aria-pressed={minutes === m}
                    onClick={() => setMinutes(m)}>{m} min</button>
                ))}
              </div>
            </Dropdown>
            <div className="ml-auto">
              {running
                ? <button className="btn-ghost !h-[38px]" onClick={onStop}><Square size={14} fill="currentColor" /> Stop run</button>
                : <button className="btn-lime" onClick={start} disabled={!selected || starting || platforms.length === 0}>
                    <Play size={15} fill="currentColor" /> {starting ? "Starting…" : "Start run"}</button>}
            </div>
          </div>
          {shown && <p role="alert" className="mt-3 text-[13px] text-bad">{shown}</p>}
        </div>
      </div>
    </section>
  );
}

function Section({ id, icon, title, subtitle, state, open, onToggle, children }: {
  id: string; icon: React.ReactNode; title: string; subtitle: string; state: string; open: boolean;
  onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div>
      <button className="grid min-h-[74px] w-full grid-cols-[minmax(0,1fr)_auto] items-center px-5 py-4 text-left hover:bg-white/[0.025]"
        aria-expanded={open} aria-controls={`${id}-editor`} onClick={onToggle}>
        <span className="flex min-w-0 items-center gap-4">
          <span className="text-lime">{icon}</span>
          <span className="min-w-0">
            <span className="block text-[14px] font-medium">{title}</span>
            <span className="mt-1 block text-[12.5px] text-mist">{subtitle}</span>
          </span>
        </span>
        <span className="flex items-center gap-3 text-[12px] text-mist">
          <span className="max-sm:hidden">{state}</span>
          <ChevronDown size={16} className={`transition ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      <div className="reveal" data-open={open} id={`${id}-editor`} inert={!open}>
        <div>
          <div className="px-6 pb-6 pt-2 max-sm:px-4">{children}</div>
        </div>
      </div>
    </div>
  );
}

function Dropdown({ label, disabled, children }: { label: ReactNode; disabled?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent | globalThis.KeyboardEvent) => {
      if (e instanceof MouseEvent && ref.current?.contains(e.target as Node)) return;
      if (!(e instanceof MouseEvent) && e.key !== "Escape") return;
      setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button className="chip" aria-expanded={open} disabled={disabled} onClick={() => setOpen((o) => !o)}>{label}</button>
      {open && <div className="glass absolute left-0 top-full z-30 mt-2 w-56 rounded-xl p-3 shadow-2xl">{children}</div>}
    </div>
  );
}
