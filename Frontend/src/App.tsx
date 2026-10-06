import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Briefs from "./pages/Briefs";
import CharacterPage from "./pages/CharacterPage";
import Characters from "./pages/Characters";
import Review from "./pages/Review";
import RunLive from "./pages/RunLive";
import Runs from "./pages/Runs";
import Settings from "./pages/Settings";

const nav = [
  { to: "/runs", label: "Runs" },
  { to: "/characters", label: "Characters" },
  { to: "/briefs", label: "Briefs" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  return (
    <div className="min-h-screen md:grid md:grid-cols-[13rem_1fr]">
      <aside className="border-b border-rule bg-paper md:min-h-screen md:border-b-0 md:border-r">
        <div className="px-4 py-3 md:block md:px-5 md:py-6">
          <p className="font-marker text-xl leading-none text-grease md:text-2xl">Trend Finder</p>
          <nav className="mt-2 flex gap-1 overflow-x-auto md:mt-8 md:flex-col" aria-label="Main">
            {nav.map((n) => (
              <NavLink key={n.to} to={n.to}
                className={({ isActive }) =>
                  `shrink-0 rounded-md px-3 py-1.5 text-[0.95rem] md:py-2 font-semibold ${isActive ? "bg-ink text-paper" : "hover:bg-table"}`}>
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 md:px-10 md:py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/runs" replace />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunLive />} />
          <Route path="/runs/:id/review" element={<Review />} />
          <Route path="/characters" element={<Characters />} />
          <Route path="/characters/:slug" element={<CharacterPage />} />
          <Route path="/briefs" element={<Briefs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<p>That page doesn't exist. Pick a section on the left.</p>} />
        </Routes>
      </main>
    </div>
  );
}
