import { useMemo, useState } from "react";
import { API_BASE, PipelineRun, Tier, startPipeline } from "./api";

const VERTICALS = ["HVAC", "Plumbing", "Electrical", "Landscaping", "Auto", "Dental"];

function tierStyle(tier: Tier) {
  if (tier === "reject") return "bg-red-500/15 text-red-300 border-red-500/30";
  if (tier === "prime") return "bg-violet-500/20 text-violet-200 border-violet-400/40";
  if (tier === "solid") return "bg-amber-500/15 text-amber-200 border-amber-400/35";
  if (tier === "watch") return "bg-slate-500/15 text-slate-200 border-slate-400/30";
  return "bg-zinc-600/20 text-zinc-300 border-zinc-500/30";
}

export default function App() {
  const [vertical, setVertical] = useState("Plumbing");
  const [market, setMarket] = useState("Denver, CO");
  const [headcountMin, setHeadcountMin] = useState(8);
  const [headcountMax, setHeadcountMax] = useState(45);
  const [minFit, setMinFit] = useState(58);
  const [hideSkipped, setHideSkipped] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);

  const visible = useMemo(() => {
    if (!run) return [];
    // Chains always score 0, so min fit would hide them anyway. Let the checkbox decide.
    return run.targets.filter((t) => (t.skipped ? !hideSkipped : t.fit_score >= minFit));
  }, [run, hideSkipped, minFit]);

  const counts = useMemo(() => {
    if (!run) return null;
    // Each count is rows hidden by one control, so shown + belowMin + hiddenChains = total.
    const chains = run.targets.filter((t) => t.skipped).length;
    const belowMin = run.targets.filter((t) => !t.skipped && t.fit_score < minFit).length;
    const exportable = run.targets.some((t) => t.source === "osm");
    return { total: run.targets.length, belowMin, hiddenChains: hideSkipped ? chains : 0, exportable };
  }, [run, minFit, hideSkipped]);

  async function onAnalyze() {
    setBusy(true);
    setErr(null);
    try {
      const data = await startPipeline({
        vertical,
        market,
        headcount_min: headcountMin,
        headcount_max: headcountMax,
        limit: 32,
      });
      setRun(data);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Error");
    } finally {
      setBusy(false);
    }
  }

  function downloadCsv() {
    if (!run) return;
    const params = new URLSearchParams({
      min_score: String(minFit),
      include_rejects: String(!hideSkipped),
    });
    window.open(`${API_BASE}/v1/pipeline/runs/${run.id}/export?${params}`, "_blank");
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-ink via-[#12121a] to-ink">
      <header className="border-b border-line px-6 py-5">
        <div className="mx-auto flex max-w-5xl flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-fog">SMB acquisition</p>
            <h1 className="text-2xl font-semibold">
              Deal <span className="text-violet">Prioritizer</span>
            </h1>
          </div>
          <p className="max-w-md text-sm text-fog">
            Score local operators before spending enrichment credits or caller hours.
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <section className="rounded-2xl border border-line bg-panel/90 p-6 shadow-lg shadow-black/30">
          <h2 className="mb-4 text-lg font-semibold">Search criteria</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-sm text-fog">
              Vertical
              <select
                className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2 text-white"
                value={vertical}
                onChange={(e) => setVertical(e.target.value)}
              >
                {VERTICALS.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label className="text-sm text-fog sm:col-span-2">
              Market
              <input
                className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2 text-white"
                value={market}
                onChange={(e) => setMarket(e.target.value)}
              />
            </label>
            <label className="text-sm text-fog">
              Headcount min <span className="text-xs">(for enrichment)</span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2 text-white"
                value={headcountMin}
                onChange={(e) => setHeadcountMin(Number(e.target.value))}
              />
            </label>
            <label className="text-sm text-fog">
              Headcount max <span className="text-xs">(for enrichment)</span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2 text-white"
                value={headcountMax}
                onChange={(e) => setHeadcountMax(Number(e.target.value))}
              />
            </label>
          </div>
          <p className="mt-2 text-xs text-fog">
            OpenStreetMap doesn't publish staff counts, so the headcount band is saved with each run for a
            future enrichment source (e.g. Google Places) and doesn't change scores yet.
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={onAnalyze}
            className="mt-5 rounded-xl bg-violet px-5 py-2.5 font-semibold text-white hover:bg-violet/90 disabled:opacity-50"
          >
            {busy ? "Analyzing…" : "Run prioritization"}
          </button>
          {err && <p className="mt-3 text-sm text-red-400">{err}</p>}
        </section>

        <section className="rounded-2xl border border-line bg-panel/90 p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">Ranked targets</h2>
            <div className="flex flex-wrap items-center gap-4 text-sm text-fog">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={hideSkipped} onChange={(e) => setHideSkipped(e.target.checked)} />
                Hide chains
              </label>
              <label>
                Min fit
                <input
                  type="number"
                  className="ml-2 w-16 rounded border border-line bg-ink px-2 py-1 text-white"
                  value={minFit}
                  onChange={(e) => setMinFit(Number(e.target.value))}
                />
              </label>
              <button
                type="button"
                disabled={!counts?.exportable}
                title={run && !counts?.exportable ? "Sample companies are not exported" : undefined}
                onClick={downloadCsv}
                className="rounded-lg border border-amber/50 px-3 py-1 text-amber hover:bg-amber/10 disabled:opacity-40"
              >
                Download CSV
              </button>
            </div>
          </div>

          {run?.source_status === "unavailable" && (
            <div className="mb-4 rounded-xl border border-amber/40 bg-amber/10 p-4 text-sm text-amber-100">
              <p className="font-semibold text-amber">Live data unavailable</p>
              <p className="mt-1">
                OpenStreetMap is busy or rate-limiting requests, so these are{" "}
                <strong>sample companies, not real businesses</strong>. They are never exported. Run the
                search again in a minute.
              </p>
              {run.source_detail && <p className="mt-2 text-xs text-amber-100/70">Details: {run.source_detail}</p>}
            </div>
          )}

          {run?.source_status === "no_results" && (
            <p className="mb-4 rounded-xl border border-line p-4 text-sm text-fog">
              No {run.vertical} businesses found in OpenStreetMap near {run.market}. Try a nearby city or
              another vertical.
            </p>
          )}

          {counts && (
            <p className="mb-2 text-sm text-fog">
              Showing <span className="font-semibold text-white">{visible.length}</span> of {counts.total}{" "}
              companies · <span className="text-white">{counts.belowMin}</span> below min fit {minFit} ·{" "}
              <span className="text-white">{counts.hiddenChains}</span> chains hidden
            </p>
          )}

          {!run && (
            <p className="rounded-xl border border-dashed border-line p-8 text-center text-fog">
              Run prioritization to populate the pipeline.
            </p>
          )}

          {run && visible.length === 0 && (
            <p className="text-sm text-amber">No companies match these filters. Lower min fit or untick Hide chains.</p>
          )}

          {visible.length > 0 && (
            <ul className="divide-y divide-line">
              {visible.map((t) => (
                <li key={t.id} className="flex flex-col gap-2 py-4 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <p className="font-medium">{t.legal_name}</p>
                    <p className="text-xs text-fog">
                      {[t.street_line, t.locality, t.region].filter(Boolean).join(", ")}
                    </p>
                    <p className="mt-1 text-sm text-fog">{t.rationale}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3 text-sm">
                    <span className="text-fog">{t.main_phone || t.email || "No contact listed"}</span>
                    {t.source === "sample" && (
                      <span className="rounded-full border border-dashed border-amber/60 px-2.5 py-0.5 text-xs font-semibold uppercase text-amber">
                        Sample
                      </span>
                    )}
                    <span
                      className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase ${tierStyle(t.tier)}`}
                    >
                      {t.tier} · {Math.round(t.fit_score)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}
