import { ChevronDown, Loader2, Plus, X } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useSocialActions, useSocialReport, useSocials } from "../api/hooks";
import { ChannelView, type Window } from "../components/socials/ChannelView";
import { PLATFORM_LABEL, PlatformIcon } from "../components/ui";

const WINDOWS: { id: Window; label: string }[] = [{ id: "7d", label: "7 days" }, { id: "30d", label: "30 days" }, { id: "all", label: "All" }];

/** Our own channels, one at a time (a switch, not a stack), all on the dark page. */
export default function Socials() {
  const [params, setParams] = useSearchParams();
  const overview = useSocials();
  const chars = overview.data?.characters ?? [];
  const slug = params.get("c") ?? chars.find((c) => c.channels.length)?.slug ?? chars[0]?.slug ?? null;
  const current = chars.find((c) => c.slug === slug);
  const channels = current?.channels ?? [];
  const [adding, setAdding] = useState(false);
  const selected = channels.find((c) => c.id === params.get("ch")) ?? channels[0];
  const window = (["7d", "30d", "all"].includes(params.get("w") ?? "") ? params.get("w") : "30d") as Window;
  const set = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const gone of ["connected", "error"]) next.delete(gone);
    for (const [k, v] of Object.entries(values)) if (v == null) next.delete(k); else next.set(k, v);
    setParams(next, { replace: true });
  };
  const connected = params.get("connected"), failed = params.get("error");
  const free = (["instagram", "tiktok"] as const).filter((p) => !channels.some((c) => c.platform === p));

  return (
    <section className="wrap pb-24 pt-10">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <h1 className="text-[30px] font-semibold">Socials</h1>
          <p className="mt-1 text-[13px] text-mist">Your own channels, read every hour. Never posted to or logged in to.</p>
        </div>
        {chars.length > 1 && (
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Character">
            {chars.map((c) => (
              <button key={c.slug} role="tab" aria-selected={c.slug === slug} className="chip" aria-pressed={c.slug === slug}
                onClick={() => set({ c: c.slug, ch: null })}>{c.name}</button>
            ))}
          </div>
        )}
      </div>
      {connected && <Banner tone="ok" onClose={() => set({})}>{PLATFORM_LABEL[connected as "tiktok"] ?? connected} is connected through the official API.</Banner>}
      {failed && <Banner tone="bad" onClose={() => set({})}>{failed}</Banner>}
      {overview.isLoading && <p className="mt-8 text-[13px] text-mist">Loading…</p>}

      {current && overview.data && (
        <>
          <div className="mt-7 flex flex-wrap items-center gap-3 border-b border-line pb-4">
            <div className="flex gap-1 rounded-xl bg-panel/60 p-1" role="tablist" aria-label="Channel">
              {channels.map((c) => {
                const on = c.id === selected?.id && !adding;
                return (
                  <button key={c.id} role="tab" aria-selected={on} onClick={() => { setAdding(false); set({ ch: c.id }); }}
                    className={`flex h-9 items-center gap-2 rounded-lg px-3.5 text-[13px] transition ${on ? "bg-snow font-medium text-night" : "text-mist hover:text-snow"}`}>
                    <PlatformIcon platform={c.platform} size={13} /> @{c.handle}
                    {c.mode === "api" && <span className={`h-1.5 w-1.5 rounded-full ${on ? "bg-lime-deep" : "bg-lime"}`} title="Official API" />}
                  </button>
                );
              })}
              {free.length > 0 && (
                <button onClick={() => setAdding((a) => !a)} aria-pressed={adding}
                  className={`flex h-9 items-center gap-1.5 rounded-lg px-3 text-[13px] transition ${adding ? "bg-snow font-medium text-night" : "text-mist hover:text-snow"}`}>
                  <Plus size={14} /> Add</button>
              )}
            </div>
            {!adding && selected && (
              <div className="ml-auto flex gap-1 rounded-full bg-panel/60 p-1" role="radiogroup" aria-label="Window">
                {WINDOWS.map((w) => (
                  <button key={w.id} role="radio" aria-checked={window === w.id} onClick={() => set({ w: w.id })}
                    className={`rounded-full px-3 py-1 text-[12.5px] ${window === w.id ? "bg-snow font-medium text-night" : "text-mist hover:text-snow"}`}>{w.label}</button>
                ))}
              </div>
            )}
          </div>

          <div className="mt-6">
            {(adding || !selected)
              ? <AddChannel slug={current.slug} name={current.name} free={free} onAdded={(id) => { setAdding(false); set({ ch: id }); }} />
              : <ChannelView key={selected.id} channel={selected} overview={overview.data} window={window} slug={current.slug} />}
          </div>
          {channels.length > 0 && <Report slug={current.slug} />}
        </>
      )}
    </section>
  );
}

function Banner({ tone, children, onClose }: { tone: "ok" | "bad"; children: React.ReactNode; onClose: () => void }) {
  return (
    <p role={tone === "bad" ? "alert" : "status"} className={`mt-5 flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] ${tone === "ok" ? "bg-lime/15 text-lime" : "bg-bad/15 text-bad"}`}>
      <span className="flex-1">{children}</span>
      <button onClick={onClose} aria-label="Dismiss" className="opacity-70 hover:opacity-100"><X size={14} /></button>
    </p>
  );
}

function AddChannel({ slug, name, free, onAdded }: { slug: string; name: string; free: ("instagram" | "tiktok")[];
  onAdded: (id: string) => void }) {
  const { add } = useSocialActions();
  const [platform, setPlatform] = useState<"instagram" | "tiktok">(free[0] ?? "instagram");
  const [handle, setHandle] = useState("");
  if (!free.length) return null;
  const chosen = free.includes(platform) ? platform : free[0];
  return (
    <form className="max-w-[640px] rounded-2xl border border-line bg-panel/40 p-5"
      onSubmit={(e) => { e.preventDefault(); add.mutate({ platform: chosen, handle: handle.trim(), character: slug }, { onSuccess: (c) => { setHandle(""); onAdded(c.id); } }); }}>
      <p className="text-[15px] font-semibold">Add {name}'s channel</p>
      <p className="mt-1 text-[12.5px] text-mist">Public numbers come in right away; connect the official API afterwards for the rest.</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <div className="flex gap-1" role="radiogroup" aria-label="Platform">
          {free.map((p) => (
            <button key={p} type="button" role="radio" aria-checked={chosen === p} className="chip" aria-pressed={chosen === p}
              onClick={() => setPlatform(p)}><PlatformIcon platform={p} size={12} /> {PLATFORM_LABEL[p]}</button>
          ))}
        </div>
        <input className="field !h-[30px] min-w-[200px] flex-1" value={handle} onChange={(e) => setHandle(e.target.value)} autoComplete="off"
          placeholder="@username or profile link" aria-label="Username or profile link" />
        <button className="btn-lime !h-[30px]" disabled={!handle.trim() || add.isPending}>
          {add.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} {add.isPending ? "Reading…" : "Add"}</button>
      </div>
      {add.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(add.error as Error).message}</p>}
    </form>
  );
}

function Report({ slug }: { slug: string }) {
  const [open, setOpen] = useState(false);
  const report = useSocialReport(open ? slug : null);
  return (
    <section className="mt-10 border-t border-line pt-4">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex items-center gap-2 text-[13px] text-mist hover:text-snow">
        <ChevronDown size={15} className={`transition ${open ? "rotate-180" : ""}`} /> What the agents learn from your channels</button>
      {open && (
        <div className="mt-3 rounded-xl border border-line bg-panel/40 p-4">
          <p className="text-[12px] text-mist">Every run's judges read exactly this. Formats your audience rewards count for more; it's never a verdict on your videos.</p>
          <pre className="mt-2 whitespace-pre-wrap font-[inherit] text-[12.5px] leading-relaxed text-snow/90">{report.data?.text ?? "…"}</pre>
        </div>
      )}
    </section>
  );
}
