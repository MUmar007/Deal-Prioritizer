// Only set when the UI and API are on different hosts (Render). Locally and in
// Docker, /v1 goes through the Vite or nginx proxy.
export const API_BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export type Tier = "prime" | "solid" | "watch" | "low" | "reject";
export type SourceStatus = "live" | "no_results" | "unavailable";

export type TargetRow = {
  id: string;
  legal_name: string;
  street_line: string | null;
  locality: string | null;
  region: string | null;
  main_phone: string | null;
  email: string | null;
  web_url: string | null;
  fit_score: number;
  tier: Tier;
  rationale: string;
  skipped: boolean;
  source: "osm" | "sample";
};

export type PipelineRun = {
  id: string;
  vertical: string;
  market: string;
  headcount_min: number;
  headcount_max: number;
  source_status: SourceStatus;
  source_detail: string | null;
  created_at: string;
  targets: TargetRow[];
};

type ValidationIssue = { loc: (string | number)[]; msg: string };
type ApiError = { detail?: string | ValidationIssue[] };

function errorMessage(body: ApiError): string {
  const { detail } = body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const { loc, msg } = detail[0];
    const field = loc[loc.length - 1];
    return field === "body" ? msg : `${field}: ${msg}`;
  }
  return "Pipeline failed";
}

export async function startPipeline(payload: {
  vertical: string;
  market: string;
  headcount_min: number;
  headcount_max: number;
  limit?: number;
}): Promise<PipelineRun> {
  const res = await fetch(`${API_BASE}/v1/pipeline/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body: ApiError = await res.json().catch(() => ({}));
    throw new Error(errorMessage(body));
  }
  return res.json();
}
