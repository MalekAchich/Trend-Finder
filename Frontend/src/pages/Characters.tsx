import { useSearchParams } from "react-router-dom";
import { useCharacter, useCharacterVideos, useCharacters } from "../api/hooks";
import { LibraryTabs, type LibraryTab } from "../components/Library";
import { ManualPanel } from "../components/ManualVideos";
import { VideoGrid } from "../components/VideoGrid";

const WINDOWS = [{ label: "Today", days: 1 }, { label: "7 days", days: 7 }, { label: "30 days", days: 30 },
  { label: "All", days: null }] as const;

export default function Characters() {
  const [params, setParams] = useSearchParams();
  const characters = useCharacters();
  const list = characters.data ?? [];
  const slug = params.get("c") ?? list[0]?.slug ?? null;
  const days = params.get("days") ? Number(params.get("days")) : null;
  const tab: LibraryTab = params.get("tab") === "manual" ? "manual" : "found";
  const detail = useCharacter(slug);
  const videos = useCharacterVideos(slug, days);
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v == null) next.delete(k); else next.set(k, v);
    setParams(next, { replace: true });
  };
  const d = detail.data;
  return (
    <>
      <section className="wrap pb-12 pt-10">
        <h1 className="text-[30px] font-semibold">Characters</h1>
        <p className="mt-1 text-[13px] text-mist">A character is the images in its folder in AI Influencers Characters. Everything else, the agents work out.</p>
        <div className="mt-7 flex flex-wrap gap-4" role="tablist" aria-label="Character">
          {list.map((c) => {
            const on = c.slug === slug;
            return (
              <button key={c.slug} role="tab" aria-selected={on} onClick={() => set("c", c.slug)}
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
        {d && (
          <div className="mt-10 grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
            <div>
              <h2 className="text-[15px] font-semibold">Images</h2>
              <ul className="mt-3 flex flex-wrap gap-3">
                {d.images.map((src) => (
                  <li key={src} className="overflow-hidden rounded-lg border border-line bg-white">
                    <a href={src} target="_blank" rel="noreferrer"><img src={src} alt="" className="block h-40 w-auto object-contain" /></a>
                  </li>
                ))}
              </ul>
              <dl className="num mt-5 flex gap-8 text-[13px]">
                <div><dt className="text-mist">Runs</dt><dd className="text-[20px] font-semibold">{d.runs}</dd></div>
                <div><dt className="text-mist">Videos found</dt><dd className="text-[20px] font-semibold">{d.videos_found}</dd></div>
              </dl>
            </div>
            <div className="space-y-6">
              <section className="rounded-2xl border border-line bg-panel/40 p-5">
                <h2 className="text-[15px] font-semibold">What the agents see</h2>
                {d.latest_read ? (
                  <dl className="mt-3 grid gap-2 text-[13.5px] leading-relaxed">
                    <div><dt className="inline text-mist">Look: </dt><dd className="inline">{d.latest_read.look}</dd></div>
                    <div><dt className="inline text-mist">Vibe: </dt><dd className="inline">{d.latest_read.vibe}</dd></div>
                    <div><dt className="inline text-mist">On camera: </dt><dd className="inline">{d.latest_read.performance_angle}</dd></div>
                    <div><dt className="inline text-mist">For Kling: </dt><dd className="inline">{d.latest_read.kling_constraints}</dd></div>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {d.latest_read.possible_niches.map((n) => <span key={n} className="chip !h-6 !text-[11.5px] !text-snow">{n}</span>)}
                    </div>
                  </dl>
                ) : <p className="mt-2 text-[13px] text-mist">The agents read the images at the start of the first run.</p>}
              </section>
              <section className="rounded-2xl border border-line bg-panel/40 p-5">
                <h2 className="text-[15px] font-semibold">What they learned from your ratings</h2>
                {d.taste_md
                  ? <div className="mt-3 whitespace-pre-line text-[13.5px] leading-relaxed text-snow/90">{d.taste_md.replace(/^#+\s*/gm, "")}</div>
                  : <p className="mt-2 text-[13px] text-mist">Rate a few found videos and the next run will start learning from them.</p>}
              </section>
            </div>
          </div>
        )}
      </section>
      <div className="seam" data-running={false} aria-hidden><div className="seam-grain" /></div>
      <section className="bg-paper pb-24 text-ink" aria-label="Videos for this character">
        <div className="wrap">
          <div className="flex flex-wrap items-end justify-between gap-4 pb-6 pt-2">
            <div>
              <LibraryTabs tab={tab} onTab={(t) => set("tab", t === "found" ? null : t)} />
              <p className="mt-2 text-[13px] text-ink-2">{tab === "found"
                ? `Every video the agents saved${d ? ` for ${d.name}` : ""}, newest first.`
                : `Your references (with what was learned for ${d?.name ?? "this character"}) and targets.`}</p>
            </div>
            {tab === "found" && <div className="flex gap-1 rounded-full bg-paper-2 p-1" role="radiogroup" aria-label="Found">
              {WINDOWS.map((w) => (
                <button key={w.label} role="radio" aria-checked={days === w.days}
                  onClick={() => set("days", w.days == null ? null : String(w.days))}
                  className={`rounded-full px-3 py-1 text-[13px] ${days === w.days ? "bg-white font-medium shadow-sm" : "text-ink-2 hover:text-ink"}`}>
                  {w.label}</button>
              ))}
            </div>}
          </div>
          {tab === "manual" && <ManualPanel characters={characters.data ?? []} slug={slug} />}
          {tab === "found" && videos.isSuccess && (videos.data?.length ?? 0) === 0 && (
            <div className="rounded-2xl border border-dashed border-rule px-6 py-14 text-center text-[13px] text-ink-2">
              {days ? "Nothing found in this window. Try a longer one." : "No videos yet. Start a run from Home."}</div>
          )}
          {tab === "found" && <VideoGrid videos={videos.data ?? []} />}
        </div>
      </section>
    </>
  );
}
