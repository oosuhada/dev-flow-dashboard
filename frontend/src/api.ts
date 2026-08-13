import type { PullRelation, PullRequestInput } from "./graphModel";

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
  pullRelations: PullRelation[];
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

export type PullComment = { id: number | string; user: string; body: string; createdAt?: string | null; updatedAt?: string | null; url?: string | null };
export type PullReviewComment = PullComment & { path?: string | null; line?: number | null; side?: string | null; commitId?: string | null; diffHunk?: string };
export type PullReview = { id: number | string; user: string; state: string; body: string; submittedAt?: string | null; commitId?: string | null; url?: string | null };
export type PullCommit = { sha: string; message: string; author: string; timestamp?: string | null; url?: string | null; parents: string[] };
export type PullEvent = { id: number | string; event: string; createdAt?: string | null; actor: string; commitId?: string | null; label?: string | null; requestedReviewer?: string | null; rename?: { from?: string; to?: string } | null };
export type PullCheck = { name: string; status: string; conclusion?: string | null; url?: string | null; startedAt?: string | null; completedAt?: string | null };
export type PullDetail = Omit<PullRequestInput, "reviews" | "checks"> & {
  body: string;
  comments: PullComment[];
  reviewComments: PullReviewComment[];
  reviews: PullReview[];
  commits: PullCommit[];
  events: PullEvent[];
  checks: PullCheck[];
  stats: { commits: number; additions: number; deletions: number; changedFiles: number; comments: number; reviewComments: number };
};

export type AIPriorityItem = {
  rank: number;
  number: number;
  priority: "P0" | "P1" | "P2" | "P3" | string;
  score: number;
  reason: string;
  nextAction: string;
  actor: "author" | "reviewer" | "maintainer" | "team" | string;
  impact: string;
  confidence: number;
};

export type AIPriorityState = {
  status: "ready" | "analyzing" | "disabled" | string;
  repository: string;
  model: string;
  generatedAt?: string;
  headline?: string;
  summary?: string;
  changesSinceLast?: string[];
  priorities?: AIPriorityItem[];
  watch?: Array<{ number: number; signal: string }>;
  contextSummary?: string;
  trigger?: { event?: string; action?: string | null; number?: number | null };
  usage?: Record<string, unknown>;
};

export type AICommitAnalysis = {
  status: "ready" | "disabled" | string;
  repository: string;
  sha: string;
  model: string;
  generatedAt?: string;
  summary?: string;
  riskLevel?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | string;
  whyItMatters?: string;
  affectedAreas?: string[];
  reviewFocus?: string[];
  relatedPulls?: number[];
  recommendedNextStep?: string;
  confidence?: number;
};

export type AIProjectTeamAction = {
  name: string;
  github: string;
  role: string;
  now: string;
  whyNow: string;
  nextHandoff: string;
  blockedBy: string[];
  relatedWork: Array<{ repository: string; pr: number }>;
  confidence: number;
};

export type AIProjectPriority = {
  repository: string;
  number: number;
  rank: number;
  priority: "P0" | "P1" | "P2" | "P3" | string;
  reason: string;
  nextAction: string;
  actorGithub: string;
  impact: string;
  confidence: number;
};

export type AIProjectState = {
  status: "ready" | "analyzing" | "disabled" | string;
  model: string;
  generatedAt?: string;
  headline?: string;
  projectHealth?: "ON_TRACK" | "BLOCKED" | "DRIFT" | "OVERENGINEERING" | string;
  healthReason?: string;
  currentStep?: { number: number; name: string; confidence: number; why: string; exitGate: string };
  currentObjective?: string;
  antiPatternAlerts?: Array<{ severity: "info" | "warning" | "critical" | string; title: string; reason: string; stopDoing: string; doInstead: string }>;
  teamActions?: AIProjectTeamAction[];
  prPriorities?: AIProjectPriority[];
  changesSinceLast?: string[];
  contextSummary?: string;
  trigger?: { repository?: string; event?: string; action?: string | null; number?: number | null };
  analysisSequence?: number;
  projectMemoryRevision?: string;
};

export type AIChatMessage = { role: "user" | "assistant"; content: string };
export type AIChatResponse = {
  answer: string;
  suggestedQuestions?: string[];
  model: string;
  generatedAt: string;
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

export async function loadPull(repo: string, number: number): Promise<PullDetail> {
  const params = new URLSearchParams({ repo, number: String(number) });
  return json<PullDetail>(`${API_BASE}/pull?${params}`);
}

export async function loadFile(repo: string, sha: string, path: string): Promise<FileContent> {
  const params = new URLSearchParams({ repo, sha, path });
  return json<FileContent>(`${API_BASE}/file?${params}`);
}

export async function loadAIPriority(repo: string, force = false): Promise<AIPriorityState> {
  const params = new URLSearchParams({ repo, force: String(force) });
  return json<AIPriorityState>(`${API_BASE}/ai/priority?${params}`);
}

export async function loadAICommit(repo: string, sha: string): Promise<AICommitAnalysis> {
  const params = new URLSearchParams({ repo, sha });
  return json<AICommitAnalysis>(`${API_BASE}/ai/commit?${params}`);
}

export async function loadAIProject(force = false): Promise<AIProjectState> {
  const params = new URLSearchParams({ force: String(force) });
  return json<AIProjectState>(`${API_BASE}/ai/project?${params}`);
}

export async function askAIProject(question: string, history: AIChatMessage[]): Promise<AIChatResponse> {
  const response = await fetch(`${API_BASE}/ai/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<AIChatResponse>;
}
