import type { PullRequestInput } from "./graphModel";

const API_BASE = "/dev_dashboard/api";

export type CommitRecord = {
  sha: string;
  message: string;
  author: string;
  timestamp: string;
  parents: string[];
  branch: string;
  prNumber?: number;
};

export type Snapshot = {
  repository: string;
  pulls: PullRequestInput[];
  commits: CommitRecord[];
  fetchedAt: string;
  rateLimit: { remaining: number | null; limit: number | null };
  authentication: "authenticated" | "public";
  cache: { hit: boolean; ttlSeconds: number };
};

async function json<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(payload.detail ?? `API ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function loadRepositories(): Promise<string[]> {
  return (await json<{ repositories: string[] }>(`${API_BASE}/repositories`)).repositories;
}

export async function loadSnapshot(repo: string, force = false): Promise<Snapshot> {
  const params = new URLSearchParams({ repo, force: String(force) });
  return json<Snapshot>(`${API_BASE}/snapshot?${params}`);
}
