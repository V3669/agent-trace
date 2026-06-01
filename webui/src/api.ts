/**
 * API client for the AgentTrace read-only HTTP API.
 * All requests go to /api/* (proxied to the Python server in dev mode).
 */

export interface SessionSummary {
  session_id: string;
  start_ts: string;
  end_ts: string;
  total_requests: number;
  agent_id: string;
  provider: string;
}

export interface WasteReport {
  session_id: string;
  total_billed_input_cost: number;
  wasted_cost: number;
  avoidable_pct: number;
  fixed_overhead_cost: number;
  total_requests: number;
  wasted_requests: number;
}

export interface TurnRank {
  request_id: number;
  recv_ts: string;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  billed_input_cost: number;
  billed_output_cost: number;
  total_cost: number;
  cause_label: string;
  usage_source: string;
  rank: number;
}

export interface ContextGrowthPoint {
  request_index: number;
  recv_ts: string;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  plain_input_tokens: number;
  billed_input_cost: number;
  cumulative_billed_cost: number;
  usage_source: string;
}

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${path} → ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listSessions: () => apiFetch<SessionSummary[]>("/api/sessions"),
  getSession: (id: string) => apiFetch<WasteReport>(`/api/sessions/${id}`),
  getTurns: (id: string, limit = 50) =>
    apiFetch<TurnRank[]>(`/api/sessions/${id}/turns?limit=${limit}`),
  getGrowth: (id: string) =>
    apiFetch<ContextGrowthPoint[]>(`/api/sessions/${id}/growth`),
  getLatestSession: () =>
    apiFetch<{ session_id: string | null }>("/api/sessions/latest"),
};
