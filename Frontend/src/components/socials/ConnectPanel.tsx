import { Check, Copy, ExternalLink, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useSocialActions } from "../../api/hooks";
import type { SocialChannel, SocialsOverview } from "../../api/types";

// password managers must never fill these: they're an app's keys, not a login
const NO_AUTOFILL = { autoComplete: "new-password", "data-1p-ignore": true, "data-lpignore": "true",
  "data-form-type": "other", spellCheck: false } as const;

/** The official-API connection, step by step, in a dialog: Instagram takes a token from Meta's dashboard,
 * TikTok a login. */
export function ConnectHelp({ channel, overview, onClose }: { channel: SocialChannel; overview: SocialsOverview;
  onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal aria-label="Connect the official API"
        className="max-h-[88vh] w-full max-w-[680px] overflow-y-auto rounded-2xl border border-line bg-night-2 p-6 text-[13px] leading-relaxed shadow-2xl">
        <button onClick={onClose} aria-label="Close" className="float-right grid h-8 w-8 place-items-center rounded-md text-mist hover:bg-panel hover:text-snow"><X size={16} /></button>
        {channel.platform === "instagram" ? <InstagramSteps channel={channel} onDone={onClose} />
          : channel.platform === "youtube" ? <YouTubeSteps channel={channel} overview={overview} />
            : <TikTokSteps channel={channel} overview={overview} />}
      </div>
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
            <input className="field !h-9 min-w-[240px] flex-1" type="password" {...NO_AUTOFILL} name="ig-access-token" value={token}
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
        <Step n={1}>Open your app on <Link href="https://developers.tiktok.com/apps/">TikTok for Developers</Link>. At the top, next to the app's name, switch from <b>Production</b> to <b>Sandbox</b> and create a sandbox (any name). Never "Submit for review": your own account doesn't need it.</Step>
        <Step n={2}>In the sandbox: <b>Products → Add products → Login Kit</b>. Then in <b>Scopes → + Add scopes</b>, add <b>user.info.profile, user.info.stats, video.list</b> (user.info.basic comes with Login Kit). If it says "Turn on Configure for Desktop", open <b>App details</b> and switch <b>Desktop</b> on under the platforms.</Step>
        <Step n={3}>
          In Login Kit, add this redirect URI exactly:
          <span className="mt-1.5 flex items-center gap-2">
            <code className="rounded bg-night px-2 py-1 text-[12px]">{overview.tiktok_redirect_uri}</code>
            <button type="button" onClick={copy} className="grid h-7 w-7 place-items-center rounded-md text-mist hover:bg-panel hover:text-snow" aria-label="Copy the redirect URI">
              {copied ? <Check size={13} /> : <Copy size={13} />}</button>
          </span>
        </Step>
        <Step n={4}>In <b>Sandbox settings → Target users → Add account</b>, log in as <b>@{channel.handle}</b> (it can be a different account from your developer account). Then press <b>Apply changes</b> at the top.</Step>
        <Step n={5}>
          {app.set ? <>Your app's keys are saved ({app.hint}). To change them, paste new ones:</> : <>Paste the sandbox's <b>Client key</b> and <b>Client secret</b>:</>}
          <form className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto]" onSubmit={(e) => { e.preventDefault();
            tiktokApp.mutate({ client_key: key.trim(), client_secret: secret.trim() }, { onSuccess: () => { setKey(""); setSecret(""); } }); }}>
            <input className="field !h-9" {...NO_AUTOFILL} name="tiktok-client-key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Client key" aria-label="Client key" />
            <input className="field !h-9" type="password" {...NO_AUTOFILL} name="tiktok-client-secret" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Client secret" aria-label="Client secret" />
            <button className="btn-ghost !h-9 justify-center" disabled={!key.trim() || !secret.trim() || tiktokApp.isPending}>Save keys</button>
          </form>
          {tiktokApp.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(tiktokApp.error as Error).message}</p>}
        </Step>
        <Step n={6}>
          Press Connect and allow access <b>as @{channel.handle}</b>. If TikTok shows your other account, switch to @{channel.handle} first: the app refuses any other account.
          <div className="mt-2">
            <a href={`/api/socials/tiktok/connect?channel=${channel.id}`}
              className={`btn-lime !h-9 ${app.set ? "" : "pointer-events-none opacity-45"}`} aria-disabled={!app.set}>Connect with TikTok</a>
          </div>
        </Step>
      </ol>
    </>
  );
}

function YouTubeSteps({ channel, overview }: { channel: SocialChannel; overview: SocialsOverview }) {
  const { googleApp } = useSocialActions();
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const app = overview.google_app;
  return (
    <>
      <p className="font-medium">Connect @{channel.handle} through your Google account</p>
      <p className="mt-1 text-[12.5px] text-mist">Free, about 10 minutes once. Read-only: the app can't upload, edit or delete anything.</p>
      <ol className="mt-3 space-y-2.5">
        <Step n={1}>Open <Link href="https://console.cloud.google.com/">Google Cloud Console</Link> and pick the project of your YouTube API key (top left). In <b>APIs &amp; Services → Library</b>, search <b>YouTube Analytics API</b> and press <b>Enable</b>.</Step>
        <Step n={2}>Open <Link href="https://console.cloud.google.com/auth/overview">Google Auth Platform</Link> → <b>Get started</b>: app name <b>Trend Finder</b>, your email, audience <b>External</b>, then create.</Step>
        <Step n={3}>In <b>Audience</b>, press <b>Publish app</b> (to "In production"). Left in "Testing", Google ends the connection every 7 days.</Step>
        <Step n={4}>In <b>Clients → Create client</b>: application type <b>Desktop app</b>, name <b>Trend Finder</b>, create. Copy the <b>Client ID</b> and <b>Client secret</b>.</Step>
        <Step n={5}>
          {app.set ? <>Your Google app's keys are saved ({app.hint}). To change them, paste new ones:</> : <>Paste them here:</>}
          <form className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto]" onSubmit={(e) => { e.preventDefault();
            googleApp.mutate({ client_id: id.trim(), client_secret: secret.trim() }, { onSuccess: () => { setId(""); setSecret(""); } }); }}>
            <input className="field !h-9" {...NO_AUTOFILL} name="google-client-id" value={id} onChange={(e) => setId(e.target.value)} placeholder="Client ID" aria-label="Client ID" />
            <input className="field !h-9" type="password" {...NO_AUTOFILL} name="google-client-secret" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Client secret" aria-label="Client secret" />
            <button className="btn-ghost !h-9 justify-center" disabled={!id.trim() || !secret.trim() || googleApp.isPending}>Save keys</button>
          </form>
          {googleApp.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(googleApp.error as Error).message}</p>}
        </Step>
        <Step n={6}>
          Press Connect and choose the Google account that owns <b>@{channel.handle}</b>. Google will say it hasn't verified the app (it's your own): press <b>Advanced → Go to Trend Finder</b>, then allow both permissions. The app refuses any other channel.
          <div className="mt-2">
            <a href={`/api/socials/youtube/connect?channel=${channel.id}`}
              className={`btn-lime !h-9 ${app.set ? "" : "pointer-events-none opacity-45"}`} aria-disabled={!app.set}>Connect with Google</a>
          </div>
          <p className="mt-2 text-[12px] text-mist">It comes back to {overview.youtube_redirect_uri}: nothing to register for a Desktop app.</p>
        </Step>
      </ol>
    </>
  );
}
