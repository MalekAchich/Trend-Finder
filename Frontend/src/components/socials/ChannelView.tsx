import { AlertTriangle, CircleHelp, ExternalLink, Loader2, Plug, RefreshCw, Trash2, Unplug } from "lucide-react";
import { useState } from "react";
import { useChannelTimeline, useSocialActions, useSocialPosts } from "../../api/hooks";
import type { SocialChannel, SocialPost, SocialsOverview } from "../../api/types";
import { ago, compact, formatDelta, percent, postNumbers } from "../../lib/socials";
import { PLATFORM_LABEL, PlatformIcon } from "../ui";
import { ConnectHelp } from "./ConnectPanel";
import { MiniChart } from "./MiniChart";
import { PostCard } from "./PostCard";

export type Window = "7d" | "30d" | "all";
const PROFILE: Record<string, (h: string) => string> = {
  instagram: (h) => `https://www.instagram.com/${h}/`, tiktok: (h) => `https://www.tiktok.com/@${h}`,
};
const WANTED_TIKTOK = ["user.info.basic", "user.info.profile", "user.info.stats", "video.list"];

/** Everything about one of our channels: how it's read, its numbers, its growth, its audience and its posts. */
export function ChannelView({ channel, overview, window, slug }: { channel: SocialChannel; overview: SocialsOverview;
  window: Window; slug: string }) {
  const { sync, disconnect, remove } = useSocialActions();
  const [help, setHelp] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const posts = useSocialPosts(channel.id, window);
  const all = useSocialPosts(channel.id, "all");
  const timeline = useChannelTimeline(channel.id);
  const api = channel.mode === "api";
  const reconnect = channel.last_error?.startsWith("reconnect: ");
  const missing = channel.platform === "tiktok" && api ? WANTED_TIKTOK.filter((s) => !channel.scopes.includes(s)) : [];
  const error = (sync.error ?? disconnect.error ?? remove.error) as Error | null;
  const list = posts.data ?? [];
  const numbers = postNumbers(all.data ?? []);
  const firstPost = Math.min(...(all.data ?? []).filter((p) => p.posted_at).map((p) => Date.parse(p.posted_at!)));
  const line = (timeline.data ?? []);
  const viewsPoints: [number, number][] = [
    ...(Number.isFinite(firstPost) ? [[firstPost, 0] as [number, number]] : []),
    ...line.filter((p) => p.views != null).map((p) => [Date.parse(p.t), p.views!] as [number, number])];
  const followerPoints = line.filter((p) => p.followers != null).map((p) => [Date.parse(p.t), p.followers!] as [number, number]);

  return (
    <section aria-label={`${PLATFORM_LABEL[channel.platform]} @${channel.handle}`}>
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <a href={PROFILE[channel.platform]?.(channel.handle)} target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1.5 text-[17px] font-semibold hover:underline">
          <PlatformIcon platform={channel.platform} size={16} /> @{channel.handle} <ExternalLink size={12} className="text-mist" /></a>
        <span className={`rounded-full px-2.5 py-0.5 text-[11.5px] font-medium ${api ? "bg-lime text-night" : "bg-panel text-mist"}`}
          title={api ? "Read through the platform's official API" : "Read from the public profile page"}>
          {api ? "Official API" : "Public numbers"}</span>
        <span className="text-[12px] text-mist">read {ago(channel.last_synced_at)} · every hour</span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button className="btn-ghost" onClick={() => sync.mutate(channel.id)} disabled={sync.isPending}>
            {sync.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} {sync.isPending ? "Reading…" : "Sync now"}</button>
          {(!api || reconnect) && <button className="btn-ghost" onClick={() => setHelp(true)}><Plug size={14} /> {api ? "Reconnect" : "Connect the official API"}</button>}
          {api && <button className="btn-ghost" onClick={() => disconnect.mutate(channel.id)} disabled={disconnect.isPending}
            title="Back to public numbers; the history stays"><Unplug size={14} /> Disconnect</button>}
          <button className="grid h-[34px] w-[34px] place-items-center rounded-lg text-mist hover:bg-panel hover:text-snow" onClick={() => setHelp(true)}
            aria-label="How to connect the official API" title="How to connect the official API"><CircleHelp size={16} /></button>
          {confirmRemove
            ? <span className="flex items-center gap-2 pl-1 text-[12.5px]">Remove it and its history?
                <button className="text-bad hover:underline" onClick={() => remove.mutate(channel.id)}>Remove</button>
                <button className="text-mist hover:text-snow" onClick={() => setConfirmRemove(false)}>Keep</button></span>
            : <button className="grid h-[34px] w-[34px] place-items-center rounded-lg text-mist hover:bg-panel hover:text-bad"
                onClick={() => setConfirmRemove(true)} aria-label={`Remove @${channel.handle}`} title="Remove this channel"><Trash2 size={15} /></button>}
        </div>
      </header>

      {(channel.last_error || missing.length > 0) && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-warn/10 px-3 py-2 text-[12.5px] text-warn">
          <AlertTriangle size={14} className="mt-px shrink-0" />
          <span>{reconnect ? `The official connection stopped working (${channel.last_error!.slice(11)}). Public numbers are still read: press Reconnect.`
            : channel.last_error ?? `TikTok didn't grant ${missing.join(", ")}: connect again and allow them.`}</span>
        </p>
      )}
      {!api && <p className="mt-2 text-[12.5px] text-mist">{channel.platform === "instagram"
        ? "Public numbers: views, likes, comments. Connect the official API for reach, shares, saves, watch time and where your audience is from."
        : "Public numbers: views, likes, comments, shares. Connect the official API for exact counts straight from TikTok."}</p>}

      <Stats channel={channel} posts={list} window={window} />
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <MiniChart title="Views, all posts" points={viewsPoints} empty="Appears after the first read." />
        <MiniChart title="Followers" points={followerPoints} empty="Appears after the first read." />
      </div>
      <Audience channel={channel} />

      <h3 className="mt-8 text-[15px] font-semibold">Posts</h3>
      {posts.isSuccess && list.length === 0
        ? <p className="mt-3 rounded-xl border border-dashed border-line-2 px-4 py-10 text-center text-[13px] text-mist">
            {channel.last_synced_at ? "No posts in this window." : "Not read yet: press Sync now."}</p>
        : <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-6">
            {list.map((p) => <PostCard key={p.id} post={p} number={numbers.get(p.id)} slug={slug} />)}
          </div>}
      {error && <p role="alert" className="mt-3 text-[12.5px] text-bad">{error.message}</p>}
      {help && <ConnectHelp channel={channel} overview={overview} onClose={() => setHelp(false)} />}
    </section>
  );
}

function Stats({ channel, posts, window }: { channel: SocialChannel; posts: SocialPost[]; window: Window }) {
  const views = posts.reduce((n, p) => n + (p.views_gained ?? 0), 0);
  const eng = posts.filter((p) => p.engagement != null);
  const watch = posts.filter((p) => p.watch_through != null);
  const ins = channel.account_insights;
  const delta = channel.followers_7d ? formatDelta(channel.followers_7d) : null;
  const tiles: [string, string, string | null][] = [
    ["Followers", compact(channel.followers), delta],
    [`Views, ${window === "all" ? "all time" : window === "7d" ? "7 days" : "30 days"}`, compact(posts.length ? views : null), null],
    ["Posts", compact(channel.posts ?? posts.length), null],
    ["Engagement", percent(eng.length ? eng.reduce((n, p) => n + p.engagement!, 0) / eng.length : null), null],
  ];
  if (channel.platform === "instagram") {  // TikTok's API never gives watch time or reach: no empty tiles for them
    tiles.push(["Watched through", percent(watch.length ? watch.reduce((n, p) => n + p.watch_through!, 0) / watch.length : null, 0), null],
      [`Reach, ${ins?.days ?? 28} days`, compact(ins?.reach), null]);
  }
  return (
    <dl className={`num mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 ${tiles.length > 4 ? "xl:grid-cols-6" : "xl:grid-cols-4"}`}>
      {tiles.map(([k, v, d]) => (
        <div key={k} className="rounded-xl border border-line bg-panel/40 px-3.5 py-2.5">
          <dt className="text-[11.5px] text-mist">{k}</dt>
          <dd className="mt-0.5 flex items-baseline gap-1.5"><span className="text-[20px] font-semibold">{v}</span>
            {d && <span className={`text-[12px] ${d.startsWith("+") ? "text-lime" : "text-mist"}`} title="in 7 days">{d}</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

const COUNTRY = typeof Intl !== "undefined" && "DisplayNames" in Intl ? new Intl.DisplayNames(undefined, { type: "region" }) : null;

const REPORTS = [
  { id: "reached", label: "Reached", hint: "where the people who saw your videos are (this month)" },
  { id: "engaged", label: "Engaged", hint: "where the people who liked, commented or shared are (this month)" },
  { id: "followers", label: "Followers", hint: "where your followers are" },
] as const;
const GROUPS = [["country", "Countries"], ["city", "Cities"], ["age", "Ages"], ["gender", "Genders"]] as const;

function place(group: string, label: string): string {
  if (group === "country" && /^[A-Z]{2}$/.test(label)) return COUNTRY?.of(label) ?? label;
  if (group === "gender") return ({ M: "Men", F: "Women", U: "Unknown" } as Record<string, string>)[label] ?? label;
  return label;
}

/** Where the audience is and who it is: Instagram's reached / engaged / followers reports. */
function Audience({ channel }: { channel: SocialChannel }) {
  const a = channel.audience ?? {};
  const available = REPORTS.filter((r) => a[r.id] && Object.keys(a[r.id]!).length);
  const [pick, setPick] = useState<(typeof REPORTS)[number]["id"] | null>(null);
  const report = REPORTS.find((r) => r.id === pick && available.includes(r)) ?? available[0];
  const message = channel.platform === "tiktok"
    ? "TikTok's API doesn't share where viewers are (TikTok Studio on your phone shows it)."
    : channel.mode !== "api" ? "Connect the official API to see where your viewers are: countries, cities, ages, genders."
      : "Meta fills this in once enough accounts have seen your videos (around 100). Nothing to do: it appears on its own.";
  return (
    <div className="mt-3 rounded-xl border border-line bg-panel/40 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[12px] font-medium text-snow">Audience</p>
        {available.length > 0 && (
          <div className="flex gap-1" role="tablist" aria-label="Audience report">
            {available.map((r) => (
              <button key={r.id} role="tab" aria-selected={r.id === report?.id} title={r.hint} onClick={() => setPick(r.id)}
                className={`rounded-full px-2.5 py-0.5 text-[11.5px] ${r.id === report?.id ? "bg-snow text-night" : "text-mist hover:text-snow"}`}>{r.label}</button>
            ))}
          </div>
        )}
        {report && <span className="text-[11.5px] text-mist">{report.hint}</span>}
      </div>
      {!report
        ? <p className="mt-1 text-[12.5px] text-mist">{message}</p>
        : <div className="mt-3 grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
            {GROUPS.filter(([g]) => a[report.id]![g]?.length).map(([g, title]) => {
              const rows = a[report.id]![g]!;
              const total = rows.reduce((n, r) => n + r[1], 0) || 1;
              return (
                <div key={g}>
                  <p className="text-[11.5px] text-mist">{title}</p>
                  <ul className="mt-1.5 space-y-1">
                    {rows.slice(0, 6).map(([label, n]) => (
                      <li key={label} className="grid grid-cols-[minmax(0,8rem)_1fr_2.5rem] items-center gap-2 text-[12px]">
                        <span className="truncate" title={place(g, label)}>{place(g, label)}</span>
                        <span className="h-1.5 rounded-full bg-night"><span className="block h-full rounded-full bg-lime" style={{ width: `${(n / total) * 100}%` }} /></span>
                        <span className="num text-right text-mist">{percent(n / total, 0)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>}
    </div>
  );
}
