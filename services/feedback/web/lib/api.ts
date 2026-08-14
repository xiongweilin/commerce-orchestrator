import type { Cluster, SOPCandidate, Ticket } from "./types";

const API = process.env.API_INTERNAL_URL ?? "http://feedback-api:8101";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`API ${path} returned ${response.status}`);
  return response.json() as Promise<T>;
}

export const api = {
  tickets: () => get<Ticket[]>("/v1/tickets?limit=180"),
  clusters: () => get<Cluster[]>("/v1/clusters"),
  sops: () => get<SOPCandidate[]>("/v1/sop-candidates"),
  report: () => get<{ payload: Record<string, unknown>; markdown: string; generation_source: string; workflow_version?: string | null }>("/v1/reports/weekly"),
  evaluation: () => get<Record<string, unknown>>("/v1/evaluation"),
  candidateEvaluation: () => get<Record<string, unknown>>("/v1/evaluation/candidate"),
  suiteEvaluation: () => get<Record<string, unknown>>("/v1/evaluation/suite"),
};
