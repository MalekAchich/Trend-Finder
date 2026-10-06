import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Brief } from "../api/types";
import { Empty, ErrorNote } from "../components/bits";

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setDone(true);
    setTimeout(() => setDone(false), 1500);
  };
  return <button className="btn shrink-0 text-sm" onClick={copy}>{done ? "Copied" : label}</button>;
}

function BriefCard({ b }: { b: Brief }) {
  const x = b.body;
  return (
    <article id={b.cluster_id} className="print scroll-mt-6 rounded-lg p-6">
      <div className="flex flex-wrap items-start gap-3">
        <h2 className="min-w-0 flex-1 text-xl">{x.title}</h2>
        <CopyButton text={b.body_md} label="Copy whole brief" />
      </div>
      <p className="mt-2">{x.concept}</p>
      <div className="mt-6 grid gap-6 md:grid-cols-2">
        <section>
          <h3>Record yourself</h3>
          <p className="mt-1 whitespace-pre-line text-[0.95rem]">{x.record_yourself}</p>
          <p className="mt-2 text-sm text-graphite">{x.framing}</p>
          <p className="num mt-2 text-sm">Use {x.motion_window.start_s.toFixed(1)}s to {x.motion_window.end_s.toFixed(1)}s of the source for the motion.</p>
        </section>
        <section>
          <div className="flex items-center gap-2">
            <h3 className="flex-1">Kling prompt</h3>
            <CopyButton text={x.kling_prompt} label="Copy prompt" />
          </div>
          <p className="mt-1 rounded-md bg-table p-3 text-[0.95rem]">{x.kling_prompt}</p>
          <p className="mt-2 text-sm text-graphite">Character orientation: match the {x.character_orientation === "video" ? "video" : "image"}.</p>
        </section>
      </div>
      {x.shots.length > 0 && (
        <section className="mt-6">
          <h3>Shots</h3>
          <ol className="mt-2 space-y-1">
            {x.shots.map((s, i) => (
              <li key={i} className="grid grid-cols-[3.5rem_1fr] gap-2 text-[0.95rem]">
                <span className="num text-graphite">{s.seconds}s</span><span>{s.description}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
      {x.risks.length > 0 && (
        <section className="mt-6">
          <h3>Watch out for</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-[0.95rem]">{x.risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </section>
      )}
      <p className="mt-6 text-xs text-graphite">Written by {b.provider ?? "an agent"} on {new Date(b.created_at).toLocaleDateString()}</p>
    </article>
  );
}

export default function Briefs() {
  const q = useQuery({ queryKey: ["briefs"], queryFn: () => api.get<Brief[]>("/api/briefs") });
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (hash && q.data) document.getElementById(hash)?.scrollIntoView();
  }, [q.data]);
  return (
    <div className="mx-auto max-w-4xl">
      <h1>Production briefs</h1>
      <p className="mt-1 text-graphite">Everything you need to film a select and generate it with Kling.</p>
      <ErrorNote error={q.error} />
      {q.data?.length === 0 && <div className="mt-6"><Empty title="No briefs yet">Select a trend card in a review, send your feedback, then write a brief for it.</Empty></div>}
      <div className="mt-6 space-y-8">{q.data?.map((b) => <BriefCard key={b.cluster_id} b={b} />)}</div>
    </div>
  );
}
