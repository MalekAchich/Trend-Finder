import { Route, Routes } from "react-router-dom";
import { useActiveRun } from "./api/hooks";
import { Navbar } from "./components/Navbar";
import Characters from "./pages/Characters";
import Home from "./pages/Home";
import RunPage from "./pages/RunPage";
import Runs from "./pages/Runs";
import Settings from "./pages/Settings";
import Socials from "./pages/Socials";

export default function App() {
  const active = useActiveRun();
  return (
    <div className="min-h-screen bg-night">
      <Navbar active={active.data ?? null} />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/characters" element={<Characters />} />
        <Route path="/runs" element={<Runs />} />
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/socials" element={<Socials />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<p className="wrap pt-10 text-mist">That page doesn't exist.</p>} />
      </Routes>
    </div>
  );
}
