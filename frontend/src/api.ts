import type { PullRequestInput } from "./graphModel";

const API_BASE = "/dev_dashboard/api";

export type CommitRecord = {
  sha: string;
  message: string;
  author: string;
  email: string;
  timestamp: string;
  parents: string[];
  branch: string;
  prNumber?: number;
  refs: Array<{ name: string; type: "head" | "tag" | "remote" }>;
};

export type Snapshot = {
  repository: string;
  defaultBranch: string;
  headSha: string | null;
  pulls: PullRequestInput[];
  commits: CommitRecord[];
  fetchedAt: string;
  rateLimit: { remaining: number | null; limit: number | null };
  authentication: "authenticated" | "public";
  cache: { hit: boolean; ttlSeconds: number };
};

export type CommitFile = {
  filename: string;
  previousFilename?: string | null;
  status: string;
  additions: number;
  deletions: number;
  changes: number;
  patch?: string | null;
  blobUrl?: string | null;
  rawUrl?: string | null;
};

export type CommitDetail = {
  sha: string;
  htmlUrl: string;
  message: string;
  author: string;
  authorEmail?: string | null;
  authoredAt?: string | null;
  committer: string;
  committedAt?: string | null;
  parents: string[];
  stats: { additions: number; deletions: number; total: number };
  files: CommitFile[];
};

export type FileContent = {
  path: string;
  sha: string;
  size: number;
  content: string;
  binary: boolean;
  truncated: boolean;
  htmlUrl: string;
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

export async function loadCommit(repo: string, sha: string): Promise<CommitDetail> {
  const params = new URLSearchParams({ repo, sha });
  return json<CommitDetail>(`${API_BASE}/commit?${params}`);
}

export async function loadFile(repo: string, sha: string, path: string): Promise<FileContent> {
  const params = new URLSearchParams({ repo, sha, path });
  return json<FileContent>(`${API_BASE}/file?${params}`);
}
