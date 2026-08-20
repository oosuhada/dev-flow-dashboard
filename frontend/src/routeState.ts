export type DashboardTab = "commits" | "pulls" | "status";
export type AIPanelTab = "pm" | "chat" | "activity";
export type PullInspectorTab = "conversation" | "commits" | "checks";
export type CommitInspectorMode = "diff" | "file";

export type DashboardRoute = {
  tab: DashboardTab;
  aiTab: AIPanelTab | null;
  pullNumber: number | null;
  pullTab: PullInspectorTab;
  commitSha: string | null;
  commitMode: CommitInspectorMode;
};

const BASE = "/dev_dashboard";

export function parseDashboardRoute(pathname: string): DashboardRoute {
  const raw = pathname.startsWith(BASE) ? pathname.slice(BASE.length) : pathname;
  const parts = raw.split("/").filter(Boolean).map(decodeURIComponent);
  const first = (parts.shift() ?? "PR").toLowerCase();
  const tab: DashboardTab = first === "graph" ? "commits" : first === "board" ? "status" : "pulls";

  let aiTab: AIPanelTab | null = null;
  const panel = (parts[0] ?? "").toLowerCase();
  if (panel === "ai-pm" || panel === "chat" || panel === "activity") {
    aiTab = panel === "ai-pm" ? "pm" : panel;
    parts.shift();
  }

  let pullNumber: number | null = null;
  let pullTab: PullInspectorTab = "conversation";
  let commitSha: string | null = null;
  let commitMode: CommitInspectorMode = "diff";

  if (tab === "pulls" && parts[0]?.toLowerCase() === "pull") {
    const number = Number(parts[1]);
    if (Number.isInteger(number) && number > 0) pullNumber = number;
    const requested = (parts[2] ?? "").toLowerCase();
    if (requested === "commits" || requested === "checks") pullTab = requested;
  }
  if (tab === "commits" && parts[0]?.toLowerCase() === "commit" && parts[1]) {
    commitSha = parts[1];
    if ((parts[2] ?? "").toLowerCase() === "file") commitMode = "file";
  }

  return { tab, aiTab, pullNumber, pullTab, commitSha, commitMode };
}

export function buildDashboardPath(route: DashboardRoute): string {
  const parts = [route.tab === "commits" ? "graph" : route.tab === "status" ? "board" : "PR"];
  if (route.aiTab) parts.push(route.aiTab === "pm" ? "ai-pm" : route.aiTab);
  if (route.tab === "pulls" && route.pullNumber) {
    parts.push("pull", String(route.pullNumber), route.pullTab);
  }
  if (route.tab === "commits" && route.commitSha) {
    parts.push("commit", route.commitSha, route.commitMode);
  }
  return `${BASE}/${parts.map(encodeURIComponent).join("/")}`;
}

