import { Loader2, Plus } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useSocialActions, useSocialPosts, useSocialReport, useSocials } from "../api/hooks";
import type { SocialChannel, SocialPost } from "../api/types";
import { ChannelCard } from "../components/socials/ChannelCard";
import { PostCard } from "../components/socials/PostCard";
import { ViewsChart } from "../components/socials/ViewsChart";
import { PLATFORM_LABEL, PlatformIcon } from "../components/ui";
import { compact, percent, postNumbers } from "../lib/socials";

type Window = "7d" | "30d" | "all";
const WINDOWS: { id: Window; label: string }[] = [{ id: "7d", label: "7 days" }, { id: "30d", label: "30 days" }, { id: "all", label: "All" }];

export default function Socials() {
  const [params, setParams] = useSearchParams();
  const overview = useSocials();
  const chars = overview.data?.characters ?? [];
  const slug = params.get("c") ?? chars.find((c) => c.channels.length)?.slug ?? chars[0]?.slug ?? null;
  const current = chars.find((c) => c.slug === slug);
  const window = (["7d", "30d", "all"].includes(params.get("w") ?? "") ? params.get("w") : "30d") as Window;
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    for (const gone of ["connected", "error"]) next.delete(gone);
    if (v == null) next.delete(k); else next.set(k, v);
    setParams(next, { replace: true });
  };
  const connected = params.get("connected");
  const failed = params.get("error");
  return (
    <>
      <section className="wrap pb-12 pt-10">
        <h1 className="text-[30px] font-semibold">Socials</h1>
        <p className="mt-1 max-w-[760px] text-[13px] text-mist">Your characters' own channels and how every post does. The numbers are read, never posted or logged in to, and the agents learn from what your audience rewards.</p>
        {connected && <p role="status" className="mt-5 rounded-lg bg-lime/15 px-3 py-2 text-[13px] text-lime">{PLATFORM_LABEL[connected as "tiktok"] ?? connected} is connected through the official API. Its numbers are being read.</p>}
        {failed && <p role="alert" className="mt-5 rounded-lg bg-bad/15 px-3 py-2 text-[13px] text-bad">{failed}</p>}
        {chars.length > 1 && (
          <div className="mt-6 flex flex-wrap gap-2" role="tablist" aria-label="Character">
            {chars.map((c) => (
              <button key={c.slug} role="tab" aria-selected={c.slug === slug} className="chip" aria-pressed={c.slug === slug}
                onClick={() => set("c", c.slug)}>{c.name}{c.channels.length ? ` · ${c.channels.length}` : ""}</button>
            ))}
          </div>
        )}
        {overview.isLoading && <p className="mt-8 text-[13px] text-mist">Loading…</p>}
        {current && overview.data && (
          <>
            <div className="mt-7 grid gap-4 lg:grid-cols-2">
              {current.channels.map((ch) => <ChannelCard key={ch.id} channel={ch} overview={overview.data!} />)}
              <AddChannel slug={current.slug} name={current.name} taken={current.channels.map((c) => c.platform)} />
            </div>
            <Report slug={current.slug} hasChannels={current.channels.length > 0} />
          </>
        )}
      </section>
      <div className="seam" data-running={false} aria-hidden><div className="seam-grain" /></div>
      <section className="bg-paper pb-24 text-ink" aria-label="Posts and numbers">
        <div className="wrap">
          <div className="flex flex-wrap items-end justify-between gap-4 pb-6 pt-2">
            <div>
              <h2 className="text-[24px] font-semibold">Posts</h2>
              <p className="mt-1 text-[13px] text-ink-2">Every post with its latest numbers, read every 3 hours. "–" means the platform doesn't give that number.</p>
            </div>
            <div className="flex gap-1 rounded-full bg-paper-2 p-1" role="radiogroup" aria-label="Window">
              {WINDOWS.map((w) => (
                <button key={w.id} role="radio" aria-checked={window === w.id} onClick={() => set("w", w.id)}
                  className={`rounded-full px-3 py-1 text-[13px] ${window === w.id ? "bg-white font-medium shadow-sm" : "text-ink-2 hover:text-ink"}`}>{w.label}</button>
              ))}
            </div>
          </div>
          {current && current.channels.length === 0 && (
            <div className="rounded-2xl border border-dashed border-rule px-6 py-14 text-center text-[13px] text-ink-2">
              Add {current.name}'s Instagram or TikTok above: the posts and their numbers show up here.</div>
          )}
          <div className="space-y-12">
            {current?.channels.map((ch) => <ChannelPosts key={ch.id} channel={ch} window={window} slug={current.slug} />)}
          </div>
        </div>
      </section>
    </>
  );
}

function ChannelPosts({ channel, window, slug }: { channel: SocialChannel; window: Window; slug: string }) {
  const posts = useSocialPosts(channel.id, window);
  const all = useSocialPosts(channel.id, "all");
  const list = posts.data ?? [];
  const numbers = postNumbers(all.data ?? []);
  return (
    <section aria-label={`${PLATFORM_LABEL[channel.platform]} @${channel.handle}`} className={posts.isFetching ? "opacity-70 transition" : "transition"}>
      <h3 className="flex items-center gap-2 text-[16px] font-semibold"><PlatformIcon platform={channel.platform} size={15} /> @{channel.handle}</h3>
      {posts.isSuccess && list.length === 0
        ? <p className="mt-4 rounded-xl border border-dashed border-rule px-4 py-10 text-center text-[13px] text-ink-2">
            {channel.last_synced_at ? "No posts in this window yet." : "Not read yet: press Sync now above."}</p>
        : <>
            <Headline posts={list} />
            <div className="mt-5"><ViewsChart posts={all.data ?? list} /></div>
            <div className="mt-6 grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-6">
              {list.map((p) => <PostCard key={p.id} post={p} number={numbers.get(p.id)} slug={slug} />)}
            </div>
          </>}
    </section>
  );
}

function Headline({ posts }: { posts: SocialPost[] }) {
  const views = posts.reduce((n, p) => n + (p.views_gained ?? 0), 0);
  const withEng = posts.filter((p) => p.engagement != null);
  const withWatch = posts.filter((p) => p.watch_through != null);
  const best = [...posts].sort((a, b) => (b.views ?? 0) - (a.views ?? 0))[0];
  const tiles: [string, string][] = [
    ["Views in this window", compact(posts.length ? views : null)],
    ["Posts", String(posts.length)],
    ["Engagement (avg)", percent(withEng.length ? withEng.reduce((n, p) => n + p.engagement!, 0) / withEng.length : null)],
    ["Watched through (avg)", percent(withWatch.length ? withWatch.reduce((n, p) => n + p.watch_through!, 0) / withWatch.length : null, 0)],
    ["Best post", best ? `${compact(best.views)} views` : "–"],
  ];
  return (
    <dl className="num mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {tiles.map(([k, v]) => (
        <div key={k} className="rounded-xl bg-card px-4 py-3 shadow-[0_1px_0_var(--color-rule)]">
          <dt className="text-[12px] text-ink-2">{k}</dt><dd className="mt-0.5 text-[22px] font-semibold">{v}</dd></div>
      ))}
    </dl>
  );
}

function AddChannel({ slug, name, taken }: { slug: string; name: string; taken: string[] }) {
  const { add } = useSocialActions();
  const free = (["instagram", "tiktok"] as const).filter((p) => !taken.includes(p));
  const [platform, setPlatform] = useState<"instagram" | "tiktok">(free[0] ?? "instagram");
  const [handle, setHandle] = useState("");
  if (!free.length) return null;
  const chosen = free.includes(platform) ? platform : free[0];
  return (
    <form className="flex flex-col justify-center rounded-2xl border border-dashed border-line-2 p-5"
      onSubmit={(e) => { e.preventDefault(); add.mutate({ platform: chosen, handle: handle.trim(), character: slug }, { onSuccess: () => setHandle("") }); }}>
      <p className="text-[15px] font-semibold">Add {name}'s channel</p>
      <p className="mt-1 text-[12.5px] text-mist">Its public numbers show up right away; connect the official API afterwards for the rest.</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <div className="flex gap-1" role="radiogroup" aria-label="Platform">
          {free.map((p) => (
            <button key={p} type="button" role="radio" aria-checked={chosen === p} className="chip" aria-pressed={chosen === p}
              onClick={() => setPlatform(p)}><PlatformIcon platform={p} size={12} /> {PLATFORM_LABEL[p]}</button>
          ))}
        </div>
        <input className="field !h-[30px] min-w-[200px] flex-1" value={handle} onChange={(e) => setHandle(e.target.value)}
          placeholder="@username or profile link" aria-label="Username or profile link" />
        <button className="btn-lime !h-[30px]" disabled={!handle.trim() || add.isPending}>
          {add.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} {add.isPending ? "Reading…" : "Add"}</button>
      </div>
      {add.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(add.error as Error).message}</p>}
    </form>
  );
}

function Report({ slug, hasChannels }: { slug: string; hasChannels: boolean }) {
  const report = useSocialReport(hasChannels ? slug : null);
  if (!hasChannels) return null;
  return (
    <section className="mt-6 rounded-2xl border border-line bg-panel/40 p-5">
      <h2 className="text-[15px] font-semibold">What the agents learn from your channels</h2>
      <p className="mt-1 text-[12.5px] text-mist">Every run's judges read this. Formats your audience rewards count for more; nothing here is a verdict on your videos.</p>
      {report.data?.text
        ? <pre className="mt-3 whitespace-pre-wrap font-[inherit] text-[13px] leading-relaxed text-snow/90">{report.data.text}</pre>
        : <p className="mt-3 text-[13px] text-mist">Appears after the first read.</p>}
    </section>
  );
}
