import { ExternalLink, KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import { useAccountAction, useAccounts, useModelSettings, usePriority, useSaveModels, useSavePriority, useUsage } from "../api/hooks";
import type { AccountItem, ModelChoice, ProviderUsage } from "../api/types";
import { BrandLogo } from "../components/brand";

const NAME: Record<string, string> = { claude: "Claude", chatgpt: "ChatGPT" };
function resetText(epoch: number): string {
  const d = new Date(epoch * 1000);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}
const at = (epoch: number | null) => epoch ? new Date(epoch * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : null;

export default function Settings() {
  const usage = useUsage();
  const models = useModelSettings();
  return (
    <section className="wrap pb-24 pt-10">
      <h1 className="text-[30px] font-semibold">Settings</h1>
      <h2 className="mt-9 text-[15px] font-semibold">Usage</h2>
      <p className="mt-1 text-[13px] text-mist">Your subscriptions' 5-hour and weekly limits, as their websites show them. ChatGPT is read live; Claude is refreshed at most every 10 minutes.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {(usage.data?.providers ?? []).map((u) => <UsageCard key={u.provider} u={u} />)}
      </div>
      <Priority />
      <Accounts />
      <h2 className="mt-12 text-[15px] font-semibold">Models</h2>
      <p className="mt-1 text-[13px] text-mist">The main model does the thinking (lead agent, reader, analyst, scouts); the fast one does quick steps (trend radar). New calls use your choice right away.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {models.data && Object.entries(models.data).map(([p, m]) => <ModelCard key={p} provider={p} choice={m} />)}
      </div>
    </section>
  );
}

function Priority() {
  const priority = usePriority();
  const save = useSavePriority();
  const first = save.isPending ? save.variables : priority.data?.first ?? null;
  const options = [{ value: null as string | null, label: "Balanced", hint: "Each agent uses the subscription it's tuned for, and searches alternate between both to spread the usage." },
    ...(priority.data?.providers ?? []).map((p) => ({ value: p as string | null, label: `${NAME[p] ?? p} first`,
      hint: `Every agent uses ${NAME[p] ?? p}. The other subscription takes over only while ${NAME[p] ?? p} is at its usage limit.` }))];
  const picked = options.find((o) => o.value === first) ?? options[0];
  return (
    <>
      <h2 className="mt-12 text-[15px] font-semibold">Provider priority</h2>
      <p className="mt-1 text-[13px] text-mist">Which subscription the agents use first. A refused answer is never handed to the other one.</p>
      <div role="radiogroup" aria-label="Provider priority" className="mt-4 inline-flex rounded-xl border border-line bg-panel/40 p-1">
        {options.map((o) => (
          <button key={o.label} type="button" role="radio" aria-checked={o.value === first} disabled={!priority.data || save.isPending}
            onClick={() => o.value !== first && save.mutate(o.value)}
            className={`h-9 rounded-lg px-4 text-[13px] transition-colors ${o.value === first ? "bg-snow font-medium text-night" : "text-mist hover:text-snow"}`}>
            {o.label}</button>
        ))}
      </div>
      <p className="mt-2.5 text-[12.5px] text-mist">{picked.hint}</p>
      {save.isError && <p role="alert" className="mt-1 text-[12.5px] text-bad">{(save.error as Error).message}</p>}
    </>
  );
}

function UsageCard({ u }: { u: ProviderUsage }) {
  const cooling = u.status === "cooling";
  const label = u.status === "auth_error" ? "Sign-in needed: run tf login" : cooling
    ? `At its limit${at(u.cooling_until) ? ` until ${at(u.cooling_until)}` : ""}` : "Ready";
  return (
    <div className="rounded-2xl border border-line bg-panel/40 p-5">
      <div className="flex items-center justify-between">
        <p className="text-[15px] font-medium">{NAME[u.provider] ?? u.provider}</p>
        <span className={`flex items-center gap-1.5 text-[12.5px] ${cooling ? "text-warn" : u.status === "ok" ? "text-lime" : "text-bad"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${cooling ? "bg-warn" : u.status === "ok" ? "bg-lime" : "bg-bad"}`} />{label}</span>
      </div>
      {u.windows.length > 0 ? (
        <div className="mt-4 space-y-3">
          {u.windows.map((w) => (
            <div key={w.name}>
              <div className="flex justify-between text-[12.5px] text-mist">
                <span>{w.name === "seven_day" ? "Weekly limit" : w.name === "five_hour" ? "5-hour limit" : w.name}</span>
                <span className="num">{Math.round(w.used_percent)}% used{at(w.resets_at) ? `, resets ${resetText(w.resets_at!)}` : ""}</span>
              </div>
              <div className="mt-1.5 h-2 rounded-full bg-white/10">
                <div className={`h-2 rounded-full ${w.used_percent > 85 ? "bg-warn" : "bg-lime"}`} style={{ width: `${Math.min(w.used_percent, 100)}%` }} />
              </div>
            </div>
          ))}
        </div>
      ) : <p className="mt-4 text-[12.5px] text-mist">{cooling && u.last_error ? u.last_error : "Shown after the next call to this subscription."}</p>}
      <dl className="num mt-5 grid grid-cols-3 gap-3 text-[12.5px]">
        <div><dt className="text-mist">Tokens, last 24 h</dt><dd className="mt-0.5 text-[17px] font-semibold">{u.tokens_today.toLocaleString()}</dd></div>
        <div><dt className="text-mist">Calls, last 24 h</dt><dd className="mt-0.5 text-[17px] font-semibold">{u.calls_today.toLocaleString()}</dd></div>
        <div><dt className="text-mist">Average per run</dt><dd className="mt-0.5 text-[17px] font-semibold">{u.avg_tokens_per_run == null ? "–" : u.avg_tokens_per_run.toLocaleString()}</dd></div>
      </dl>
    </div>
  );
}

function ModelCard({ provider, choice }: { provider: string; choice: ModelChoice }) {
  const save = useSaveModels();
  const [main, setMain] = useState(choice.main ?? "");
  const [fast, setFast] = useState(choice.fast ?? "");
  const [effort, setEffort] = useState(choice.effort ?? "");
  useEffect(() => { setMain(choice.main ?? ""); setFast(choice.fast ?? ""); setEffort(choice.effort ?? ""); }, [choice]);
  const offered = (id: string) => choice.models.some((m) => m.id === id);
  const efforts = (() => {
    if (provider === "claude") return choice.efforts;
    const lv = [main, fast].map((id) => choice.models.find((m) => m.id === id)?.efforts ?? []);
    return lv[0].filter((x) => lv[1].includes(x));
  })();
  const dirty = main !== (choice.main ?? "") || fast !== (choice.fast ?? "") || effort !== (choice.effort ?? "");
  const options = (value: string) => (
    <>
      {!offered(value) && value && <option value={value}>{value} (not offered now)</option>}
      {choice.models.map((m) => (
        <option key={m.id} value={m.id} disabled={!!m.unavailable}>{m.name}{m.unavailable ? ` (${m.unavailable})` : ""}</option>
      ))}
    </>
  );
  return (
    <form className="rounded-2xl border border-line bg-panel/40 p-5"
      onSubmit={(e) => { e.preventDefault(); save.mutate({ provider, main, fast, effort: effort || null }); }}>
      <p className="text-[15px] font-medium">{NAME[provider] ?? provider}</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <label className="text-[12.5px] text-mist">Main model
          <select className="field mt-1" value={main} onChange={(e) => setMain(e.target.value)}>{options(main)}</select></label>
        <label className="text-[12.5px] text-mist">Fast model
          <select className="field mt-1" value={fast} onChange={(e) => setFast(e.target.value)}>{options(fast)}</select></label>
        <label className="text-[12.5px] text-mist">Effort
          <select className="field mt-1" value={efforts.includes(effort) ? effort : ""} onChange={(e) => setEffort(e.target.value)}
            disabled={efforts.length === 0}>
            <option value="">{efforts.length ? "Per agent (default)" : "Not offered"}</option>
            {efforts.map((x) => <option key={x} value={x}>{x}</option>)}
          </select></label>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-lime !h-9" disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save"}</button>
        {save.isSuccess && !dirty && <span className="text-[12.5px] text-mist">Saved. New calls use it.</span>}
        {save.isError && <span role="alert" className="text-[12.5px] text-bad">{(save.error as Error).message}</span>}
      </div>
    </form>
  );
}

const UNLOCKS: Record<string, string> = {
  youtube_api_key: "Shorts search sorted by views or date, with exact stats. Free, about 95 searches a day.",
  tiktok: "TikTok's own search: fresh, sorted results instead of what search engines indexed.",
  instagram: "Reel stats, video analysis and Instagram's own search.",
  x: "Search for video posts on X.",
};
const ACCOUNT_PLATFORM: Record<string, string> = { youtube_api_key: "youtube", tiktok: "tiktok", instagram: "instagram", x: "x" };
const since = (iso: string | null) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : null;

function Accounts() {
  const accounts = useAccounts();
  return (
    <>
      <h2 className="mt-12 text-[15px] font-semibold">Accounts &amp; keys</h2>
      <p className="mt-1 max-w-3xl text-[13px] text-mist">What the search agents can use. Everything stays on this computer, in the app's
        secrets folder: this page only ever shows the last 4 characters. Use separate scraping accounts, never your main
        accounts or the ones you'll post with.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {(accounts.data?.items ?? []).map((a) => a.kind === "key" ? <KeyCard key={a.id} a={a} /> : <SessionCard key={a.id} a={a} />)}
      </div>
    </>
  );
}

function CardHead({ a, state, tone }: { a: AccountItem; state: string; tone: "ok" | "warn" | "off" }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <p className="flex items-center gap-2.5 text-[15px] font-medium">
        <BrandLogo platform={ACCOUNT_PLATFORM[a.id]} size={16} tone="brand" />{a.label}</p>
      <span className={`flex shrink-0 items-center gap-1.5 text-[12.5px] ${tone === "ok" ? "text-lime" : tone === "warn" ? "text-warn" : "text-mist"}`}>
        <span className={`h-1.5 w-1.5 rounded-full ${tone === "ok" ? "bg-lime" : tone === "warn" ? "bg-warn" : "bg-white/25"}`} />{state}</span>
    </div>
  );
}

function KeyCard({ a }: { a: AccountItem }) {
  const { setKey, remove } = useAccountAction();
  const [value, setValue] = useState("");
  const busy = setKey.isPending || remove.isPending;
  return (
    <form className="rounded-2xl border border-line bg-panel/40 p-5" onSubmit={(e) => {
      e.preventDefault();
      if (value.trim()) setKey.mutate({ id: a.id, value }, { onSuccess: () => setValue("") });
    }}>
      <CardHead a={a} state={a.set ? `Set${since(a.updated_at) ? `, ${since(a.updated_at)}` : ""}` : "Not set"} tone={a.set ? "ok" : "off"} />
      <p className="mt-2 text-[12.5px] text-mist">{UNLOCKS[a.id]}</p>
      {a.set && <p className="num mt-3 flex items-center gap-2 text-[13px]"><KeyRound size={13} className="text-mist" />{a.hint}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <input className="field min-w-0 flex-1" type="password" autoComplete="off" spellCheck={false} value={value}
          onChange={(e) => setValue(e.target.value)} placeholder={a.set ? "Paste a new key to replace it" : "Paste the key"}
          aria-label={a.label} />
        <button className="btn-lime !h-[38px]" disabled={!value.trim() || busy}>{setKey.isPending ? "Checking…" : a.set ? "Replace" : "Save"}</button>
        {a.set && <button type="button" className="btn-ghost !h-[38px]" disabled={busy} onClick={() => remove.mutate(a.id)}>Remove</button>}
      </div>
      {setKey.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(setKey.error as Error).message}</p>}
      {a.id === "youtube_api_key" && (
        <a className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-mist hover:text-snow" target="_blank" rel="noreferrer"
          href="https://console.cloud.google.com/apis/library/youtube.googleapis.com">
          Get a key: enable YouTube Data API v3, then create an API key <ExternalLink size={12} /></a>
      )}
    </form>
  );
}

function SessionCard({ a }: { a: AccountItem }) {
  const { connect, remove } = useAccountAction();
  const waiting = a.connecting?.state === "waiting";
  const failed = a.connecting?.state === "failed";
  const [state, tone] = waiting ? ["Waiting for you to log in", "warn"] as const
    : a.status === "connected" ? [`Connected${since(a.updated_at) ? ` since ${since(a.updated_at)}` : ""}`, "ok"] as const
    : a.status === "expired" ? ["Session expired", "warn"] as const : ["Not connected", "off"] as const;
  return (
    <div className="rounded-2xl border border-line bg-panel/40 p-5">
      <CardHead a={a} state={state} tone={tone} />
      <p className="mt-2 text-[12.5px] text-mist">{UNLOCKS[a.id]}</p>
      {waiting && <p className="mt-3 text-[13px]">A browser window opened on this computer. Log in there; this card updates by itself.</p>}
      {failed && <p role="alert" className="mt-3 text-[12.5px] text-bad">{a.connecting?.message}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="btn-lime !h-9" disabled={waiting || connect.isPending} onClick={() => connect.mutate(a.id)}>
          {a.status === "not connected" ? "Connect" : "Reconnect"}</button>
        {a.set && <button className="btn-ghost !h-9" disabled={waiting || remove.isPending} onClick={() => remove.mutate(a.id)}>Disconnect</button>}
      </div>
      {connect.isError && <p role="alert" className="mt-2 text-[12.5px] text-bad">{(connect.error as Error).message}</p>}
    </div>
  );
}
