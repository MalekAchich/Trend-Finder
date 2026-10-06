import { useEffect, useRef, useState } from "react";
import svg from "../Logo/trend-finder-animated.svg?raw";
import { createCycle } from "../lib/cycle";

const CYCLE_MS = 3600; // the SVG's intro takes ~3 s; restart it once it settles

/** The animated logo: the intro plays once when the app opens, and replays in a loop while an agent run is active. */
export function Logo({ running }: { running: boolean }) {
  const [cycle, setCycle] = useState(0);
  const ref = useRef(createCycle(CYCLE_MS, () => setCycle((c) => c + 1)));
  useEffect(() => {
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    ref.current.set(running && !reduced);
  }, [running]);
  useEffect(() => () => ref.current.dispose(), []);
  return (
    <span key={cycle} className="tf-logo block" data-running={running} aria-label="Trend Finder"
      // the file is our own static asset, inlined so its CSS animation runs and can be restarted
      dangerouslySetInnerHTML={{ __html: svg }} />
  );
}
