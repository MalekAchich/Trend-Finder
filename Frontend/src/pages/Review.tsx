import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Rating, TrendCard, TrendsResponse } from "../api/types";
import { GreaseMark } from "../components/GreaseMark";
import { ago, Empty, ErrorNote, ScoreBar, views } from "../components/bits";
import { initialReview, keyToAction, reviewReducer, type CardRating } from "../review/reducer";

interface FeedbackOut {
  taste_version: number | null;
  retrospective: string[];
  primary_niche: string | null;
  weight_suggestion: Record<string, number> | null;
  learner_error: string | null;
}

const fitLabels: Record<string, string> = {
  persona: "Persona", deadpan_contrast: "Deadpan contrast", energy: "Energy", niche: "Niche", adaptability: "Adaptability",
};

function Sheet({ card, rating }: { card: TrendCard; rating?: Rating }) {
  return (
    <div className="print flex justify-center self-start rounded-sm p-2">
      <div className="relative">
        {card.contact_sheet_url
          ? <img src={card.contact_sheet_url} alt={`Frames from ${card.best.creator ?? "the source video"}`}
              className="block max-h-[calc(100vh-9rem)] w-auto max-w-full" />
          : <div className="grid aspect-[4/3] w-80 place-items-center text-graphite">No frames for this video</div>}
        {rating && <GreaseMark key={rating} rating={rating} />}
      </div>
    </div>
  );
}

function Facts({ card }: { card: TrendCard }) {
  const v = card.best;
  return (
    <ul className="num flex flex-wrap gap-x-4 gap-y-1 text-sm text-graphite">
      <li className="capitalize">{v.platform}</li>
      {v.creator && <li>@{v.creator}</li>}
      <li>{views(v.metrics.views)} views</li>
      <li>{ago(v.posted_at)}</li>
      {v.duration_s != null && <li>{Math.round(v.duration_s)}s long</li>}
      {card.member_count > 1 && <li>{card.member_count} copies found</li>}
    </ul>
  );
}

function Decision({ card, rating, draft, noteRef, onNote, onRate }: {
  card: TrendCard; rating?: CardRating; draft: string; noteRef: React.RefObject<HTMLTextAreaElement | null>;
  onNote: (s: string) => void; onRate: (r: Rating) => void;
}) {
  const s = card.scores;
  return (
    <div className="space-y-5">
      <div>
        <p className="num text-sm text-graphite">Rank {card.rank}</p>
        <h2 className="mt-1 text-xl">{card.label ?? card.best.caption ?? "Untitled trend"}</h2>
        <div className="mt-2"><Facts card={card} /></div>
        <a href={card.best.url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm font-semibold underline">
          Watch the original on {card.best.platform}</a>
      </div>
      <div className="flex items-end gap-4">
        <p className="num text-5xl font-bold leading-none" style={{ fontVariationSettings: '"wdth" 125' }}>
          {s.overall == null ? "–" : Math.round(s.overall)}</p>
        <p className="pb-1 text-sm text-graphite">overall match out of 100</p>
      </div>
      <div className="grid grid-cols-2 gap-x-5 gap-y-3">
        <ScoreBar label="Fits the character" value={s.fit} />
        <ScoreBar label="Easy to recreate" value={s.feasibility} />
        <ScoreBar label="Growing fast" value={s.momentum} />
        <ScoreBar label="Still fresh" value={s.freshness} />
      </div>
      {card.disagreement && card.cross_check && (
        <p className="rounded-md border border-grease/40 px-3 py-2 text-sm">
          The second opinion disagreed and scored the fit {Math.round(card.cross_check.fit)}: {card.cross_check.justification}
        </p>
      )}
      {card.adaptation_idea && <section><h3>How to adapt it</h3><p className="mt-1">{card.adaptation_idea}</p></section>}
      {card.justification && <section><h3>Why it fits</h3><p className="mt-1 text-[0.95rem]">{card.justification}</p></section>}
      {card.motion_window && (
        <p className="num text-sm">Best part to copy the motion from: {card.motion_window.start_s.toFixed(1)}s to {card.motion_window.end_s.toFixed(1)}s</p>
      )}
      {card.feasibility_notes && <p className="text-sm text-graphite">{card.feasibility_notes}</p>}
      {card.fit_breakdown && (
        <details className="text-sm">
          <summary className="cursor-pointer text-graphite">Fit breakdown</summary>
          <ul className="num mt-2 grid grid-cols-2 gap-1">
            {Object.entries(card.fit_breakdown).map(([k, v]) => <li key={k}>{fitLabels[k] ?? k}: {v}/10</li>)}
          </ul>
        </details>
      )}
      <div className="border-t border-rule pt-4">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Your verdict">
          {([["up", "Select it", "U"], ["down", "Pass", "D"], ["skip", "Decide later", "S"]] as const).map(([r, label, key]) => (
            <button key={r} onClick={() => onRate(r)} aria-pressed={rating?.rating === r}
              className={`btn ${rating?.rating === r ? (r === "skip" ? "bg-ink text-paper" : "btn-primary") : ""}`}>
              {label} <kbd className="text-xs opacity-60">{key}</kbd>
            </button>
          ))}
        </div>
        <label className="mt-3 block text-sm font-semibold">Note for the agents <span className="font-normal text-graphite">(optional, press N)</span>
          <textarea ref={noteRef} className="field mt-1 h-20 resize-y font-normal" maxLength={1000} value={draft}
            placeholder="What made you pick or pass it"
            onChange={(e) => onNote(e.target.value)} />
        </label>
      </div>
    </div>
  );
}

export function FilmStrip({ cards, index, ratings, onPick }: {
  cards: TrendCard[]; index: number; ratings: Record<string, CardRating>; onPick: (i: number) => void;
}) {
  const active = useRef<HTMLButtonElement>(null);
  useEffect(() => { active.current?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [index]);
  return (
    <nav aria-label="All trend cards" className="-mx-4 overflow-x-auto bg-ink px-4 py-3 md:-mx-10 md:px-10">
      <ol className="flex gap-2">
        {cards.map((c, i) => (
          <li key={c.id} className="shrink-0">
            <button ref={i === index ? active : undefined} onClick={() => onPick(i)}
              aria-label={`Card ${c.rank}${ratings[c.id] ? `, marked ${ratings[c.id].rating}` : ""}`}
              aria-current={i === index}
              className={`relative block h-16 w-24 overflow-hidden rounded-sm bg-paper ${i === index ? "ring-2 ring-grease ring-offset-2 ring-offset-ink" : "opacity-75 hover:opacity-100"}`}>
              {c.contact_sheet_url && <img src={c.contact_sheet_url} alt="" className="h-full w-full object-cover" />}
              <span className="num absolute left-1 top-0.5 rounded bg-paper/85 px-1 text-xs font-semibold">{c.rank}</span>
              {ratings[c.id] && <GreaseMark rating={ratings[c.id].rating} size="small" />}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}

function Wrapup({ runId, cards, ratings, existing }: {
  runId: string; cards: TrendCard[]; ratings: Record<string, CardRating>;
  existing: TrendsResponse["run_feedback"];
}) {
  const qc = useQueryClient();
  const [satisfaction, setSatisfaction] = useState(existing?.satisfaction ?? 6);
  const [note, setNote] = useState(existing?.note ?? "");
  const submit = useMutation({
    mutationFn: () => api.post<FeedbackOut>(`/api/runs/${runId}/feedback`, {
      cards: cards.filter((c) => ratings[c.id]).map((c) => ({ cluster_id: c.id, ...ratings[c.id] })),
      satisfaction, note: note.trim() || null,
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trends", runId] }),
  });
  const brief = useMutation({
    mutationFn: (id: string) => api.post(`/api/trends/${id}/brief`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["trends", runId] }); qc.invalidateQueries({ queryKey: ["briefs"] }); },
  });
  const selected = cards.filter((c) => c.feedback?.rating === "up");
  const out = submit.data;
  return (
    <section className="print rounded-lg p-5">
      <h2>How useful was this run?</h2>
      <p className="mt-1 text-sm text-graphite">Your ratings and this score tune what the agents look for next time.</p>
      <div className="mt-4 flex items-center gap-4">
        <input type="range" min={1} max={10} value={satisfaction} onChange={(e) => setSatisfaction(Number(e.target.value))}
          className="w-full max-w-sm accent-grease" aria-label="Satisfaction from 1 to 10" />
        <span className="num text-2xl font-bold">{satisfaction}<span className="text-base font-normal text-graphite"> / 10</span></span>
      </div>
      <textarea className="field mt-3 h-20" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)}
        placeholder="Anything the agents should do differently next run" aria-label="Run note" />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" onClick={() => submit.mutate()} disabled={submit.isPending}>
          {submit.isPending ? "Teaching the agents…" : existing ? "Update feedback" : "Send feedback"}</button>
        {existing && !out && <span className="text-sm text-graphite">Feedback saved. You can change it and send again.</span>}
      </div>
      <ErrorNote error={submit.error} />
      {out && (
        <div className="mt-4 space-y-2 text-sm">
          <p className="font-semibold">Feedback saved.{out.taste_version ? ` Taste profile is now version ${out.taste_version}.` : ""}</p>
          {out.learner_error && <p className="text-grease">The taste profile wasn't updated: {out.learner_error}. Your ratings are saved.</p>}
          {out.primary_niche && <p>The agents think the niche is: <strong>{out.primary_niche}</strong></p>}
          {out.retrospective.length > 0 && <ul className="list-disc pl-5">{out.retrospective.map((x, i) => <li key={i}>{x}</li>)}</ul>}
          {out.weight_suggestion && <p>There's a new scoring suggestion in <Link className="underline" to="/settings">Settings</Link>.</p>}
        </div>
      )}
      {selected.length > 0 && (
        <div className="mt-6 border-t border-rule pt-4">
          <h3>Production briefs for your selects</h3>
          <ul className="mt-2 space-y-2">
            {selected.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-3">
                <span className="min-w-0 flex-1 truncate">{c.label ?? c.best.caption}</span>
                {c.has_brief ? <Link className="text-sm font-semibold underline" to={`/briefs#${c.id}`}>Open brief</Link>
                  : <button className="btn" disabled={brief.isPending} onClick={() => brief.mutate(c.id)}>
                      {brief.isPending && brief.variables === c.id ? "Writing…" : "Write brief"}</button>}
              </li>
            ))}
          </ul>
          <ErrorNote error={brief.error} />
        </div>
      )}
    </section>
  );
}

function Board({ data }: { data: TrendsResponse }) {
  const cards = data.cards;
  const existing = useMemo(() => Object.fromEntries(cards.filter((c) => c.feedback)
    .map((c) => [c.id, { rating: c.feedback!.rating, note: c.feedback!.note }])), [cards]);
  const [state, dispatch] = useReducer(reviewReducer, undefined, () => initialReview(cards.map((c) => c.id), existing));
  const noteRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement;
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable;
      if (typing && e.key === "Escape") { el.blur(); return; }
      const action = keyToAction(e.key, typing);
      if (!action) return;
      e.preventDefault();
      if (action.type === "focusNote") noteRef.current?.focus();
      else dispatch(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const card = cards[state.index];
  const rated = Object.keys(state.ratings).length;
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-4">
        <h1>Review trend cards</h1>
        <p className="num text-graphite">{rated} of {cards.length} marked</p>
        <p className="ml-auto hidden text-sm text-graphite lg:block">J and K to move, U select, D pass, S later, N note, Esc leaves the note</p>
      </div>
      <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1.2fr)_minmax(20rem,1fr)]">
        <Sheet card={card} rating={state.ratings[card.id]?.rating} />
        <Decision card={card} rating={state.ratings[card.id]} draft={state.drafts[card.id] ?? ""} noteRef={noteRef}
          onNote={(note) => dispatch({ type: "note", note })} onRate={(rating) => dispatch({ type: "rate", rating })} />
      </div>
      <div className="mt-8"><FilmStrip cards={cards} index={state.index} ratings={state.ratings}
        onPick={(index) => dispatch({ type: "goto", index })} /></div>
      {(state.complete || data.run_feedback) && (
        <div className="mt-8"><Wrapup runId={data.run_id} cards={cards} ratings={state.ratings} existing={data.run_feedback} /></div>
      )}
      {!state.complete && !data.run_feedback && (
        <p className="mt-6 text-sm text-graphite">Mark every card to send your feedback. Use "Decide later" for ones you're unsure about.</p>
      )}
    </>
  );
}

export default function Review() {
  const { id } = useParams();
  const [showFiltered, setShowFiltered] = useState(false);
  const q = useQuery({
    queryKey: ["trends", id],
    queryFn: () => api.get<TrendsResponse>(`/api/runs/${id}/trends?include=filtered`),
  });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <p className="text-graphite">Loading trend cards…</p>;
  const d = q.data;
  if (d.state !== "review_ready") {
    return <Empty title="This run isn't ready for review">It's {d.state.replace("_", " ")} right now. <Link className="underline" to={`/runs/${id}`}>Watch it live</Link>.</Empty>;
  }
  if (d.cards.length === 0) {
    return <Empty title="No trend cards in this run">Every video was filtered out or no candidates were found. Start another run, maybe with different platforms.</Empty>;
  }
  return (
    <div className="mx-auto max-w-7xl">
      <Board key={d.run_id} data={d} />
      {d.filtered.length > 0 && (
        <section className="mt-10">
          <button className="text-sm font-semibold underline" onClick={() => setShowFiltered((x) => !x)}>
            {showFiltered ? "Hide" : "Show"} the {d.filtered.length} videos that were filtered out</button>
          {showFiltered && (
            <ul className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {d.filtered.map((f) => (
                <li key={f.video.canonical_id} className="rounded-md border border-rule bg-paper p-3 text-sm">
                  <a href={f.video.url} target="_blank" rel="noreferrer" className="font-semibold underline">{f.video.creator ? `@${f.video.creator}` : f.video.canonical_id}</a>
                  <p className="mt-1 text-graphite">{f.reason ?? f.status.replace("_", " ")}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
