import { Link, useSearchParams } from "react-router-dom";
import { useCharacters, useRunsHistory } from "../api/hooks";
import { STATE_TEXT, clock } from "../components/ui";

export default function Runs() {
  const [params, setParams] = useSearchParams();
  const character = params.get("c");
  const characters = useCharacters();
  const runs = useRunsHistory(character);
  return (
    <section className="wrap pb-24 pt-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[30px] font-semibold">Agent runs</h1>
          <p className="mt-1 text-[13px] text-mist">Every run, newest first. Open one to replay what the agents did and see what they saved.</p>
        </div>
        <label className="flex items-center gap-2 text-[13px] text-mist">Character
          <select className="field !h-9 !w-44" value={character ?? ""}
            onChange={(e) => setParams(e.target.value ? { c: e.target.value } : {}, { replace: true })}>
            <option value="">All characters</option>
            {(characters.data ?? []).map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
          </select>
        </label>
      </div>
      <div className="mt-7 overflow-x-auto rounded-2xl border border-line">
        <table className="w-full min-w-[720px] text-left text-[13.5px]">
          <thead className="text-[12px] text-mist">
            <tr className="border-b border-line">
              <th className="px-5 py-3 font-medium">Started</th><th className="px-3 py-3 font-medium">Character</th>
              <th className="px-3 py-3 font-medium">State</th><th className="px-3 py-3 text-right font-medium">Duration</th>
              <th className="px-3 py-3 text-right font-medium">Videos</th><th className="px-3 py-3 text-right font-medium">Score</th>
              <th className="px-5 py-3 text-right font-medium">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {(runs.data ?? []).map((r) => {
              const secs = ((r.finished_at ? Date.parse(r.finished_at) : Date.now()) - Date.parse(r.started_at)) / 1000;
              return (
                <tr key={r.id} className="border-b border-line last:border-0 hover:bg-white/[0.03]">
                  <td className="px-5 py-3"><Link to={`/runs/${r.id}`} className="font-medium hover:text-lime">
                    {new Date(r.started_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</Link></td>
                  <td className="px-3 py-3"><span className="flex items-center gap-2">
                    {r.character.image_url && <img src={r.character.image_url} alt="" className="h-6 w-6 rounded-full object-cover object-left-top" />}{r.character.name}</span></td>
                  <td className="px-3 py-3"><span className={r.active ? "text-lime" : "text-mist"}>{STATE_TEXT[r.state] ?? r.state}</span>
                    {r.stop_reason && <span className="block text-[12px] text-mist/80">{r.stop_reason}</span>}</td>
                  <td className="num px-3 py-3 text-right text-mist">{clock(secs)}</td>
                  <td className="num px-3 py-3 text-right">{r.videos}</td>
                  <td className="num px-3 py-3 text-right">{r.satisfaction ?? "–"}</td>
                  <td className="num px-5 py-3 text-right text-mist">{r.tokens.toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {runs.isSuccess && (runs.data?.length ?? 0) === 0 && <p className="px-5 py-10 text-center text-[13px] text-mist">No runs yet. Start one from Home.</p>}
      </div>
    </section>
  );
}
