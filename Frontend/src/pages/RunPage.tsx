import { ArrowLeft } from "lucide-react";
import { useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { useRun, useRunVideos, useStopRun } from "../api/hooks";
import { Engine } from "../components/Engine";
import { RunScore } from "../components/Library";
import { VideoGrid } from "../components/VideoGrid";
import { useRunStream } from "../stream/useRunStream";

export default function RunPage() {
  const { id } = useParams();
  const run = useRun(id ?? null);
  const stop = useStopRun();
  const savedStripRef = useRef<HTMLDivElement>(null);
  const { state: stream, connected } = useRunStream(id ?? null, run.data?.agents ?? []);
  const videos = useRunVideos(id ?? "", !!run.data?.active);
  if (run.isError) return <p className="wrap pt-10 text-bad">That run doesn't exist.</p>;
  if (!run.data) return <p className="wrap pt-10 text-mist">Loading the run…</p>;
  const r = run.data;
  const ended = ["review_ready", "stopped", "failed"].includes(r.state);
  return (
    <>
      <div className="wrap pb-6 pt-8">
        <Link to="/runs" className="inline-flex items-center gap-1.5 text-[13px] text-mist hover:text-snow"><ArrowLeft size={14} /> All runs</Link>
      </div>
      <Engine run={r} stream={stream} connected={connected} stopping={stop.isPending} onStop={() => stop.mutate(r.id)}
        savedStripRef={savedStripRef} />
      <div className="seam" data-running={r.active} aria-hidden><div className="seam-grain" /></div>
      <section className="bg-paper pb-24 text-ink">
        <div className="wrap">
          <h2 className="pb-6 pt-2 text-[24px] font-semibold">Videos from this run</h2>
          {videos.isSuccess && (videos.data?.length ?? 0) === 0 && <p className="py-8 text-center text-[13px] text-ink-2">This run didn't save any videos.</p>}
          <VideoGrid videos={videos.data ?? []} />
          {ended && (videos.data?.length ?? 0) > 0 && (
            <RunScore run={{ id: r.id, state: r.state, stop_reason: r.stop_reason, started_at: r.started_at,
              finished_at: r.finished_at, videos: videos.data!.length, satisfaction: r.satisfaction, run_note: r.run_note, active: r.active }} slug={r.character.slug} />
          )}
        </div>
      </section>
    </>
  );
}
