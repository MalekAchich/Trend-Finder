import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useActiveRun, useCharacters, useRun, useStartRun, useStopRun } from "../api/hooks";
import type { FoundVideo, RunDetail } from "../api/types";
import { Composer } from "../components/Composer";
import { Engine } from "../components/Engine";
import { Library, type LibraryTab } from "../components/Library";
import { Seam, type SeamHandle } from "../components/Seam";
import { useRunStream } from "../stream/useRunStream";

const LIVE = ["created", "reading_character", "studying_trends", "discovering", "planning", "running", "curating", "paused_usage"];

export default function Home() {
  const qc = useQueryClient();
  const characters = useCharacters();
  const active = useActiveRun();
  const start = useStartRun();
  const stop = useStopRun();
  const [selected, setSelected] = useState<string | null>(null);
  const [librarySlug, setLibrarySlug] = useState<string | null>(null);
  const [viewRunId, setViewRunId] = useState<string | null>(null);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
  const seam = useRef<SeamHandle>(null);
  const savedStripRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const [libraryTab, setLibraryTab] = useState<LibraryTab>("found");
  const showManual = () => {
    setLibraryTab("manual");
    gridRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const chars = characters.data ?? [];
  useEffect(() => {
    if (!selected && chars.length) setSelected(active.data?.character.slug ?? chars[0].slug);
    if (!librarySlug && chars.length) setLibrarySlug(active.data?.character.slug ?? chars[0].slug);
  }, [chars, selected, librarySlug, active.data]);
  useEffect(() => {
    if (active.data?.id) {
      setViewRunId(active.data.id);
      setSelected(active.data.character.slug);
      setLibrarySlug(active.data.character.slug);
    }
  }, [active.data?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = useRun(viewRunId);
  const runData: RunDetail | undefined = run.data;

  const onLiveSave = useCallback(async (video: FoundVideo) => {
    await seam.current?.pulse();
    setFreshIds((s) => new Set(s).add(video.id));
    qc.invalidateQueries({ queryKey: ["videos", video.run_id] });
    qc.invalidateQueries({ queryKey: ["runs"] });
  }, [qc]);

  const { state: stream, connected } = useRunStream(viewRunId, runData?.agents ?? [], onLiveSave);
  const liveState = stream.runState ?? runData?.state;
  const running = !!runData && (runData.active || (!!liveState && LIVE.includes(liveState)));

  useEffect(() => {  // a state change refreshes the run card, the library and (at the end) the curated set
    if (!viewRunId || !stream.runState) return;
    qc.invalidateQueries({ queryKey: ["run", viewRunId] });
    qc.invalidateQueries({ queryKey: ["runs"] });
    if (!LIVE.includes(stream.runState)) {
      qc.invalidateQueries({ queryKey: ["videos", viewRunId] });
      qc.invalidateQueries({ queryKey: ["run", "active"] });
    }
  }, [stream.runState, viewRunId, qc]);

  return (
    <div className="min-h-screen">
      <div className="bg-night">
        {characters.isError && (
          <p role="alert" className="wrap pt-6 text-[13px] text-bad">The app can't reach its backend. Start it with
            <code className="mx-1 rounded bg-white/10 px-1.5">uv run tf serve</code> and reload.</p>
        )}
        <Composer characters={chars} selected={selected} onSelect={(s) => { setSelected(s); setLibrarySlug(s); }}
          running={running} starting={start.isPending} error={start.error ? (start.error as Error).message : null}
          onStop={() => viewRunId && stop.mutate(viewRunId)} onShowManual={showManual}
          onStart={(body) => start.mutate(body, { onSuccess: ({ run_id }) => {
            setViewRunId(run_id); setLibrarySlug(body.character); setFreshIds(new Set());
          } })} />
        {runData && (
          <Engine run={runData} stream={stream} connected={connected} stopping={stop.isPending}
            onStop={() => stop.mutate(runData.id)} savedStripRef={savedStripRef} />
        )}
      </div>
      <Seam ref={seam} running={running} />
      <Library characters={chars} slug={librarySlug} onSlug={setLibrarySlug} activeRunId={running ? viewRunId : null}
        freshIds={freshIds} gridRef={gridRef} tab={libraryTab} onTab={setLibraryTab} />
    </div>
  );
}
