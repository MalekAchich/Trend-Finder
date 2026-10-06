import { useState } from "react";
import { useRate } from "../api/hooks";
import type { FoundVideo } from "../api/types";
import { Feedback, VideoCard } from "./VideoCard";
import { VideoSheet } from "./VideoSheet";

/** A grid of found videos with hover previews, 👍/👎 + note, and the detail sheet. */
export function VideoGrid({ videos, freshIds }: { videos: FoundVideo[]; freshIds?: Set<string> }) {
  const rate = useRate();
  const [opened, setOpened] = useState<FoundVideo | null>(null);
  const current = opened && (videos.find((v) => v.id === opened.id) ?? opened);
  return (
    <>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-6">
        {videos.map((v) => (
          <VideoCard key={v.id} video={v} fresh={freshIds?.has(v.id)} onOpen={setOpened}
            onRate={(rating, note) => v.cluster_id && rate.mutate({ clusterId: v.cluster_id, rating, note })} />
        ))}
      </div>
      {rate.isError && <p role="alert" className="mt-3 text-[12.5px] text-bad">Couldn't save that rating: {(rate.error as Error).message}</p>}
      <VideoSheet video={current} onClose={() => setOpened(null)}>
        {current && (
          <>
            <h3 className="text-[13px] font-semibold">Your verdict</h3>
            <Feedback rated={current.feedback?.rating ?? null} note={current.feedback?.note ?? null}
              rateable={!!current.cluster_id}
              onRate={(rating, note) => current.cluster_id && rate.mutate({ clusterId: current.cluster_id, rating, note })} />
          </>
        )}
      </VideoSheet>
    </>
  );
}
