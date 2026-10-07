import { ChevronDown, Link2, Settings2, Sparkles, Square, Play, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useManualActions, useManualVideos } from "../api/hooks";
import type { Character, ManualVideo, Platform, StartRun } from "../api/types";
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
  onShowManual: () => void;
}

export function Composer({ characters, selected, onSelect, running, starting, error, onStart, onStop, onShowManual }: Props) {
  const [open, setOpen] = useState<{ trending: boolean; targets: boolean }>({ trending: false, targets: false });
  const toggle = (k: "trending" | "targets") => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const manual = useManualActions();
  const saved = useManualVideos(null).data?.items ?? [];
  const refs = saved.filter((v) => v.is_reference);
  const targets = saved.filter((v) => v.target);
  const [platforms, setPlatforms] = useState<Platform[]>(["tiktok", "instagram", "youtube"]);
  const [freshness, setFreshness] = useState<StartRun["freshness"]>("week");
  const [minutes, setMinutes] = useState(60);
  const [localError, setLocalError] = useState<string | null>(null);
  const name = characters.find((c) => c.slug === selected)?.name;

  const togglePlatform = (p: Platform) =>
    setPlatforms((ps) => (ps.includes(p) ? ps.filter((x) => x !== p) : [...ps, p]));

  const start = () => {
    if (!selected) return;
    setLocalError(null);
    // reference videos and targets come from the saved list (Manually chosen videos)
    onStart({ character: selected, platforms, freshness, minutes, trend_urls: [], targets: [] });
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
          <h2 className="text-[13px] font-medium text-snow">Your reference videos</h2>
          <p className="mb-4 mt-1 text-[12.5px] text-mist">Not required, but the agents study these first and give them priority.
            Everything you add is saved under <button className="underline decoration-white/30 underline-offset-2 hover:text-snow"
              onClick={onShowManual}>Manually chosen videos</button>.</p>
          <div className="overflow-hidden rounded-xl border border-line bg-[color-mix(in_oklch,var(--color-panel)_45%,var(--color-night))]">
            <Section id="trending" icon={<Sparkles size={18} />} open={open.trending}
              title="Trend references" subtitle="Videos the agents study to learn what works and find more like them"
              state={refs.length ? `${refs.length} saved` : "None yet"} onToggle={() => toggle("trending")}>
              <LinkInput placeholder="Paste a TikTok, Instagram, YouTube or X link and press Enter" label="Reference video link"
                onAdd={(urls) => manual.add.mutateAsync({ urls, reference: true, target: null })} />
              {refs.length > 0 && (
                <ul className="mt-4 flex flex-wrap gap-2">
                  {refs.map((v) => (
                    <li key={v.id} className="chip !text-snow" title={v.problem ?? undefined}>
                      <SavedState video={v} /><PlatformIcon platform={v.platform} />
                      <span className="max-w-[240px] truncate">{shortUrl(v.url)}</span>
                      <button aria-label={`Remove ${v.url} from references`} disabled={manual.setRoles.isPending || manual.remove.isPending}
                        onClick={() => v.target ? manual.setRoles.mutate({ id: v.id, is_reference: false }) : manual.remove.mutate(v.id)}
                        className="-mr-1 rounded-full p-0.5 hover:bg-white/10"><X size={13} /></button>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <div className="border-t border-line" />
            <Section id="targets" icon={<Link2 size={18} />} open={open.targets}
              title="Targets to recreate" subtitle="Videos a character should recreate: analysed and scored in its next run"
              state={targets.length ? `${targets.length} saved` : "None yet"} onToggle={() => toggle("targets")}>
              <LinkInput placeholder="Paste a video link and press Enter" label="Target video link" characters={characters}
                defaultCharacter={selected}
                onAdd={(urls, character) => manual.add.mutateAsync({ urls, reference: false, target: character ?? selected })} />
              {targets.length > 0 && (
                <ul className="mt-4 space-y-2">
                  {targets.map((v) => (
                    <li key={v.id} className="flex items-center gap-2">
                      <span className="flex h-[38px] min-w-0 flex-1 items-center gap-2 rounded-lg border border-line-2 px-3 text-[13px]"
                        title={v.problem ?? undefined}>
                        <SavedState video={v} /><PlatformIcon platform={v.platform} />
                        <span className="truncate">{v.url}</span>
                      </span>
                      <select className="field !w-40 shrink-0" value={v.target?.slug ?? ""} aria-label={`Character for ${v.url}`}
                        disabled={manual.setRoles.isPending} onChange={(e) => manual.setRoles.mutate({ id: v.id, target: e.target.value })}>
                        {characters.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
                      </select>
                      <button className="btn-ghost !h-[38px] !px-2.5" aria-label={`Remove ${v.url} from targets`}
                        disabled={manual.setRoles.isPending || manual.remove.isPending}
                        onClick={() => v.is_reference ? manual.setRoles.mutate({ id: v.id, target: null }) : manual.remove.mutate(v.id)}>
                        <X size={15} /></button>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            {(manual.setRoles.error || manual.remove.error) && (
              <p role="alert" className="border-t border-line px-6 py-3 text-[12.5px] text-bad">
                {((manual.setRoles.error ?? manual.remove.error) as Error).message}</p>
            )}
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

/** A saved video's lookup state, as a dot: checking (pulsing), ready, or a problem (the title says why). */
function SavedState({ video }: { video: ManualVideo }) {
  const tone = video.status === "ready" ? "bg-lime" : video.status === "problem" ? "bg-bad" : "bg-mist pulse-dot";
  const label = video.status === "ready" ? "Ready" : video.status === "problem" ? `Can't open it: ${video.problem}` : "Checking";
  return <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone}`} aria-label={label} title={label} />;
}

function LinkInput({ placeholder, label, characters, defaultCharacter, onAdd }: {
  placeholder: string; label: string; characters?: Character[]; defaultCharacter?: string | null;
  onAdd: (urls: string[], character: string | null) => Promise<{ added: number }>;
}) {
  const [draft, setDraft] = useState("");
  const [character, setCharacter] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const urls = draft.split(/\s+/).map((u) => u.trim()).filter(Boolean);  // pasting several links at once also works
  const save = async () => {
    if (!urls.length || busy) return;
    const bad = urls.filter((u) => !platformOf(u));
    if (bad.length) { setProblem(`That isn't a TikTok, Instagram, YouTube or X link: ${bad[0]}`); return; }
    setBusy(true);
    try {
      await onAdd(urls, character ?? defaultCharacter ?? null);
      setDraft("");
      setProblem(null);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <div className="flex gap-2">
        <input className="field" placeholder={placeholder} value={draft} aria-label={label} spellCheck={false}
          onChange={(e) => { setDraft(e.target.value); setProblem(null); }}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />
        {characters && (
          <select className="field !w-40 shrink-0" value={character ?? defaultCharacter ?? ""} aria-label="Which character recreates it"
            onChange={(e) => setCharacter(e.target.value)}>
            {characters.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
          </select>
        )}
        <button className="btn-ghost shrink-0" onClick={() => void save()} disabled={busy || !urls.length}>
          {busy ? "Saving…" : "Add"}</button>
      </div>
      {problem && <p role="alert" className="mt-2 text-[12.5px] text-bad">{problem}</p>}
    </div>
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
