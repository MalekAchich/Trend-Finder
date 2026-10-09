import { AlertTriangle, ExternalLink, Loader2, Plug, RefreshCw, Trash2, Unplug } from "lucide-react";
import { useState } from "react";
import { useSocialActions } from "../../api/hooks";
import type { SocialChannel, SocialsOverview } from "../../api/types";
import { ago, compact, formatDelta } from "../../lib/socials";
import { PLATFORM_LABEL, PlatformIcon } from "../ui";
import { ConnectPanel } from "./ConnectPanel";

const PROFILE: Record<string, (h: string) => string> = {
  instagram: (h) => `https://www.instagram.com/${h}/`, tiktok: (h) => `https://www.tiktok.com/@${h}`,
};
const WANTED_TIKTOK = ["user.info.basic", "user.info.profile", "user.info.stats", "video.list"];

/** One of our channels: its size, how it's read (public numbers or the official API), and what to do next. */
export function ChannelCard({ channel, overview }: { channel: SocialChannel; overview: SocialsOverview }) {
  const { sync, disconnect, remove } = useSocialActions();
  const [connecting, setConnecting] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const api = channel.mode === "api";
  const missing = channel.platform === "tiktok" && api ? WANTED_TIKTOK.filter((s) => !channel.scopes.includes(s)) : [];
  const error = (sync.error ?? disconnect.error ?? remove.error) as Error | null;
  return (
    <article className="rounded-2xl border border-line bg-panel/40 p-5">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="grid h-9 w-9 place-items-center rounded-full bg-night-2 text-snow"><PlatformIcon platform={channel.platform} size={16} /></span>
        <div className="min-w-0">
          <a href={PROFILE[channel.platform]?.(channel.handle)} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 text-[15px] font-semibold hover:underline">@{channel.handle} <ExternalLink size={12} className="text-mist" /></a>
          <p className="text-[12px] text-mist">{PLATFORM_LABEL[channel.platform]} · last read {ago(channel.last_synced_at)}</p>
        </div>
        <span className={`ml-auto rounded-full px-2.5 py-1 text-[11.5px] font-medium ${api ? "bg-lime text-night" : "bg-night-2 text-mist"}`}
          title={api ? "Read through the platform's official API" : "Read from the public profile page"}>
          {api ? "Official API" : "Public numbers"}</span>
      </header>

      <dl className="num mt-5 grid grid-cols-3 gap-3">
        <Stat label="Followers" value={compact(channel.followers)} delta={formatDelta(channel.followers_7d)} hint="in 7 days" />
        <Stat label="Views, 7 days" value={compact(channel.views_7d)} />
        <Stat label="Posts" value={compact(channel.posts)} />
      </dl>

      {channel.last_error && (
        <p className="mt-4 flex items-start gap-2 rounded-lg bg-warn/10 px-3 py-2 text-[12.5px] text-warn">
          <AlertTriangle size={14} className="mt-px shrink-0" />
          <span>{channel.last_error.startsWith("reconnect: ")
            ? <>The official connection stopped working ({channel.last_error.slice(11)}). Public numbers are still read. Connect it again below.</>
            : channel.last_error}</span>
        </p>
      )}
      {missing.length > 0 && (
        <p className="mt-3 text-[12.5px] text-warn">TikTok didn't grant {missing.join(", ")}: those numbers come from the public page. Connect again and allow them.</p>
      )}
      {!api && !connecting && (
        <p className="mt-4 text-[12.5px] text-mist">{channel.platform === "instagram"
          ? "Public pages show views, likes and comments. Connect the official API for reach, shares, saves and watch time."
          : "Public pages show views, likes, comments and shares. Connect the official API for exact numbers straight from TikTok."}</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button className="btn-ghost" onClick={() => sync.mutate(channel.id)} disabled={sync.isPending}>
          {sync.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} {sync.isPending ? "Reading…" : "Sync now"}</button>
        {(!api || channel.last_error?.startsWith("reconnect: ")) && (
          <button className="btn-ghost" onClick={() => setConnecting((c) => !c)} aria-expanded={connecting}>
            <Plug size={14} /> {api ? "Reconnect" : "Connect the official API"}</button>
        )}
        {api && <button className="btn-ghost" onClick={() => disconnect.mutate(channel.id)} disabled={disconnect.isPending}
          title="Back to public numbers; the history stays"><Unplug size={14} /> Disconnect</button>}
        <span className="ml-auto">
          {confirmRemove
            ? <span className="flex items-center gap-2 text-[12.5px]">Remove it and its history?
                <button className="text-bad hover:underline" onClick={() => remove.mutate(channel.id)}>Remove</button>
                <button className="text-mist hover:text-snow" onClick={() => setConfirmRemove(false)}>Keep</button></span>
            : <button className="grid h-8 w-8 place-items-center rounded-md text-mist hover:bg-night-2 hover:text-bad"
                onClick={() => setConfirmRemove(true)} aria-label={`Remove @${channel.handle}`}><Trash2 size={14} /></button>}
        </span>
      </div>
      {connecting && <ConnectPanel channel={channel} overview={overview} onDone={() => setConnecting(false)} />}
      {error && <p role="alert" className="mt-3 text-[12.5px] text-bad">{error.message}</p>}
    </article>
  );
}

function Stat({ label, value, delta, hint }: { label: string; value: string; delta?: string | null; hint?: string }) {
  return (
    <div>
      <dt className="text-[12px] text-mist">{label}</dt>
      <dd className="mt-0.5 flex items-baseline gap-1.5">
        <span className="text-[22px] font-semibold">{value}</span>
        {delta && <span className={`text-[12px] ${delta.startsWith("+") ? "text-lime" : "text-mist"}`} title={hint}>{delta}</span>}
      </dd>
    </div>
  );
}
