import { Check, Copy, ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";
import { useSocialActions } from "../../api/hooks";
import type { SocialChannel, SocialsOverview } from "../../api/types";

/** The official-API connection, step by step: Instagram takes a token from Meta's dashboard, TikTok a login. */
export function ConnectPanel({ channel, overview, onDone }: { channel: SocialChannel; overview: SocialsOverview;
  onDone: () => void }) {
  return (
    <div className="mt-4 rounded-xl border border-line bg-night-2/60 p-4 text-[13px] leading-relaxed">
      {channel.platform === "instagram"
        ? <InstagramSteps channel={channel} onDone={onDone} />
        : <TikTokSteps channel={channel} overview={overview} />}
    </div>
  );
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="num grid h-5 w-5 shrink-0 place-items-center rounded-full bg-panel-2 text-[11px] font-semibold">{n}</span>
      <div className="min-w-0 text-snow/90">{children}</div>
    </li>
  );
}

const Link = ({ href, children }: { href: string; children: React.ReactNode }) => (
  <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-lime hover:underline">
    {children}<ExternalLink size={11} /></a>
);

function InstagramSteps({ channel, onDone }: { channel: SocialChannel; onDone: () => void }) {
  const { instagramToken } = useSocialActions();
  const [token, setToken] = useState("");
  return (
    <>
      <p className="font-medium">Connect @{channel.handle} through Instagram's official API</p>
      <p className="mt-1 text-[12.5px] text-mist">Free, about 15 minutes once. Only reading: the app never posts or logs in to your account.</p>
      <ol className="mt-3 space-y-2.5">
        <Step n={1}>In the Instagram app: <b>Settings → Account type and tools → Switch to professional account</b>, pick <b>Creator</b>. Insights only exist for professional accounts.</Step>
        <Step n={2}>Open <Link href="https://developers.facebook.com/apps/creation/">Meta for Developers</Link>, create an app, and choose the use case <b>Manage messaging and content on Instagram</b>.</Step>
        <Step n={3}>In the app: <b>App roles → Roles → Instagram testers → Add people</b>, add <b>@{channel.handle}</b>. Then accept it in Instagram: <b>Settings → Website permissions → Apps and websites → Tester invites</b>.</Step>
        <Step n={4}>Back in the app: <b>Instagram → API setup with Instagram login → Generate access tokens → Add account</b>, log in as @{channel.handle}, allow it, and copy the token it shows.</Step>
        <Step n={5}>
          Paste it here. The app checks it belongs to @{channel.handle}, keeps it only on this computer, and renews it before it expires.
          <form className="mt-2 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault();
            instagramToken.mutate({ id: channel.id, token: token.trim() }, { onSuccess: () => { setToken(""); onDone(); } }); }}>
            <input className="field !h-9 min-w-[240px] flex-1" type="password" autoComplete="off" value={token}
              onChange={(e) => setToken(e.target.value)} placeholder="Instagram access token" aria-label="Instagram access token" />
            <button className="btn-lime !h-9" disabled={token.trim().length < 20 || instagramToken.isPending}>
              {instagramToken.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Connect</button>
          </form>
          {instagramToken.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(instagramToken.error as Error).message}</p>}
        </Step>
      </ol>
    </>
  );
}

function TikTokSteps({ channel, overview }: { channel: SocialChannel; overview: SocialsOverview }) {
  const { tiktokApp } = useSocialActions();
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [copied, setCopied] = useState(false);
  const app = overview.tiktok_app;
  const copy = async () => {
    try { await navigator.clipboard.writeText(overview.tiktok_redirect_uri); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { setCopied(false); }
  };
  return (
    <>
      <p className="font-medium">Connect @{channel.handle} through TikTok's official API</p>
      <p className="mt-1 text-[12.5px] text-mist">Free, about 15 minutes once. Only reading: the app never posts or logs in to your account.</p>
      <ol className="mt-3 space-y-2.5">
        <Step n={1}>Open <Link href="https://developers.tiktok.com/apps/">TikTok for Developers</Link>, log in, and <b>Connect an app</b>. For the platform, pick <b>Desktop</b> (this app runs on your computer).</Step>
        <Step n={2}>Add the products <b>Login Kit</b> and <b>Display API</b>, with the scopes <b>user.info.basic, user.info.profile, user.info.stats, video.list</b>.</Step>
        <Step n={3}>
          In Login Kit, add this redirect URI exactly:
          <span className="mt-1.5 flex items-center gap-2">
            <code className="rounded bg-night px-2 py-1 text-[12px]">{overview.tiktok_redirect_uri}</code>
            <button type="button" onClick={copy} className="grid h-7 w-7 place-items-center rounded-md text-mist hover:bg-panel hover:text-snow" aria-label="Copy the redirect URI">
              {copied ? <Check size={13} /> : <Copy size={13} />}</button>
          </span>
        </Step>
        <Step n={4}>Switch the app to <b>Sandbox</b>, and under <b>Target users</b> add your TikTok account <b>@{channel.handle}</b>. No app review is needed for your own account.</Step>
        <Step n={5}>
          {app.set ? <>Your app's keys are saved ({app.hint}). To change them, paste new ones:</> : <>Paste the sandbox's <b>Client key</b> and <b>Client secret</b>:</>}
          <form className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto]" onSubmit={(e) => { e.preventDefault();
            tiktokApp.mutate({ client_key: key.trim(), client_secret: secret.trim() }, { onSuccess: () => { setKey(""); setSecret(""); } }); }}>
            <input className="field !h-9" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Client key" aria-label="Client key" />
            <input className="field !h-9" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Client secret" aria-label="Client secret" />
            <button className="btn-ghost !h-9 justify-center" disabled={!key.trim() || !secret.trim() || tiktokApp.isPending}>Save keys</button>
          </form>
          {tiktokApp.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(tiktokApp.error as Error).message}</p>}
        </Step>
        <Step n={6}>
          Press Connect, then allow access on TikTok's page. You'll come back here.
          <div className="mt-2">
            <a href={`/api/socials/tiktok/connect?channel=${channel.id}`}
              className={`btn-lime !h-9 ${app.set ? "" : "pointer-events-none opacity-45"}`} aria-disabled={!app.set}>Connect with TikTok</a>
          </div>
        </Step>
      </ol>
    </>
  );
}
