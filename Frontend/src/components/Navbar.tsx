import { NavLink } from "react-router-dom";
import { useProviders } from "../api/hooks";
import type { RunDetail } from "../api/types";
import { Logo } from "./Logo";
import { STATE_TEXT } from "./ui";

const LINKS = [
  { to: "/", label: "Home", end: true }, { to: "/characters", label: "Characters" }, { to: "/runs", label: "Runs" },
  { to: "/socials", label: "Socials" }, { to: "/settings", label: "Settings" },
];

export function Navbar({ active }: { active: RunDetail | null }) {
  const providers = useProviders();
  const running = !!active;
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-night/85 backdrop-blur-xl">
      <div className="flex h-[52px] items-center gap-6 px-7 max-md:gap-3 max-md:px-[18px]">
        <NavLink to="/" className="shrink-0" aria-label="Home"><Logo running={running} /></NavLink>
        <nav aria-label="Main" className="scroll-thin flex min-w-0 gap-1 overflow-x-auto">
          {LINKS.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end}
              className={({ isActive }) => `shrink-0 rounded-md px-2.5 py-1.5 text-[13px] transition
                ${isActive ? "bg-white/[0.08] text-snow" : "text-mist hover:text-snow"}`}>{l.label}</NavLink>
          ))}
        </nav>
        <div className="ml-auto flex shrink-0 items-center gap-4 text-[12.5px] text-mist">
          <span className="flex items-center gap-2 max-lg:hidden" role="status">
            <span className={`h-1.5 w-1.5 rounded-full ${running ? "bg-lime pulse-dot" : "bg-mist/60"}`} />
            {running ? `${STATE_TEXT[active!.state] ?? "Running"} for ${active!.character.name}` : "No run going"}
          </span>
          <ul className="flex items-center gap-3" aria-label="AI subscriptions">
            {(providers.data ?? []).map((p) => {
              const ok = p.connected && p.status === "ok";
              const cooling = p.status === "cooling";
              return (
                <li key={p.provider} className="flex items-center gap-1.5"
                  title={ok ? "Ready" : cooling ? "At its usage limit for now" : "Not connected: run tf login"}>
                  <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-lime" : cooling ? "bg-warn" : "bg-bad"}`} />
                  {p.provider === "chatgpt" ? "ChatGPT" : "Claude"}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </header>
  );
}
