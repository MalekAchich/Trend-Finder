import { useEffect, useState } from "react";
import { useModelSettings, useSaveModels, useUsage } from "../api/hooks";
import type { ModelChoice, ProviderUsage } from "../api/types";

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
      <h2 className="mt-12 text-[15px] font-semibold">Models</h2>
      <p className="mt-1 text-[13px] text-mist">The main model does the thinking (lead agent, reader, analyst, scouts); the fast one does quick steps (trend radar). New calls use your choice right away.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {models.data && Object.entries(models.data).map(([p, m]) => <ModelCard key={p} provider={p} choice={m} />)}
      </div>
    </section>
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
