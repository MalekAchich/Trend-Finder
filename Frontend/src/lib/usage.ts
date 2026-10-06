export interface UsageWindow { name: string; used_percent: number; window_minutes: number | null; resets_at: number | null }

/** Each provider's card reads like its own website: ChatGPT counts down what's left, Claude counts what's used. */
export function windowView(provider: string, w: UsageWindow, now = Date.now()) {
  const used = Math.round(w.used_percent);
  if (provider === "chatgpt") {
    return { label: w.name === "seven_day" ? "Weekly limit" : "5-hour limit", value: `${100 - used}% left`,
      bar: 100 - used, reset: w.resets_at ? `Resets in ${until(w.resets_at * 1000 - now)}` : null };
  }
  return { label: w.name === "seven_day" ? "This week" : "Current session", value: `${used}% used`, bar: used,
    reset: w.resets_at ? `Resets ${day(w.resets_at * 1000, now)}` : null };
}

function until(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60000));
  const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), min = m % 60;
  return d ? `${d}d ${h}h` : h ? `${h}h ${min}m` : `${min}m`;
}

function day(ms: number, now: number): string {
  const d = new Date(ms);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return d.toDateString() === new Date(now).toDateString() ? time
    : `${d.toLocaleDateString(undefined, { weekday: "long" })} ${time}`;
}
