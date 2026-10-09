import { Download, Loader2, Music } from "lucide-react";
import { useState } from "react";
import { saveFile } from "../lib/download";

type Kind = "video" | "audio";

/** Two buttons: the video with its sound, or the sound alone. The first download of a video takes a few seconds. */
export function DownloadButtons({ canonicalId, dark }: { canonicalId: string; dark?: boolean }) {
  const [busy, setBusy] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const get = async (kind: Kind) => {
    setBusy(kind);
    setError(null);
    try {
      await saveFile(`/api/download/${encodeURIComponent(canonicalId)}?kind=${kind}`, kind === "audio" ? "sound.mp3" : "video.mp4");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const button = (kind: Kind, label: string, title: string, Icon: typeof Download) => (
    <button type="button" onClick={() => get(kind)} disabled={busy !== null} title={title}
      className={`inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11.5px] transition disabled:opacity-60 ${dark
        ? "bg-panel text-mist hover:text-snow" : "bg-paper-2 text-ink-2 hover:text-ink"}`}>
      {busy === kind ? <Loader2 size={12} className="animate-spin" /> : <Icon size={12} />}{busy === kind ? "Getting it…" : label}
    </button>
  );
  return (
    <div>
      <div className="flex items-center gap-1.5">
        {button("video", "Video", "Download the video with its sound (best quality, mp4)", Download)}
        {button("audio", "Sound", "Download only the sound (mp3)", Music)}
      </div>
      {error && <p role="alert" className="mt-1 text-[11.5px] text-bad">{error}</p>}
    </div>
  );
}
