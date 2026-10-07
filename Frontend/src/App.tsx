import { useEffect, useRef } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { useActiveRun } from "./api/hooks";
import { Navbar } from "./components/Navbar";
import { createTabIcon } from "./lib/tabIcon";
import Characters from "./pages/Characters";
import Home from "./pages/Home";
import RunPage from "./pages/RunPage";
import Runs from "./pages/Runs";
import Settings from "./pages/Settings";
import Socials from "./pages/Socials";

export default function App() {
  const active = useActiveRun();
  const location = useLocation();
  const tabIcon = useRef<ReturnType<typeof createTabIcon> | null>(null);
  useEffect(() => {
    tabIcon.current = createTabIcon();
    return () => tabIcon.current?.dispose();
  }, []);
  const running = !!active.data;
  useEffect(() => tabIcon.current?.set(running), [running]);
  return (
    <div className="min-h-screen bg-night">
      <Navbar active={active.data ?? null} />
      {/* keyed by path: each page fades in from the night background when you move between pages */}
      <main key={location.pathname} className="page-in">
      <Routes location={location}>
        <Route path="/" element={<Home />} />
        <Route path="/characters" element={<Characters />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/socials" element={<Socials />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<p className="wrap pt-10 text-mist">That page doesn't exist.</p>} />
      </Routes>
      </main>
    </div>
  );
}
