import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ExternalLink, GitBranch, GitCommit, GitPullRequest, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { loadRepositories, loadSnapshot, type CommitRecord, type Snapshot } from "./api";
import { buildPullRequestGraph, rankBottlenecks, type PullRequestModel } from "./graphModel";

const STATUS_ORDER = ["READY", "BLOCKED", "WAITING", "NEEDS_REVIEW", "STALE"] as const;
const ROW_H = 74;
const LANE_GAP = 28;
const LANE_X = 26;
const statusLabel = (status: PullRequestModel["status"]) => status === "NEEDS_REVIEW" ? "NEEDS REVIEW" : status;

function statusClass(status: PullRequestModel["status"]) {
  return status.toLowerCase().replace("_", "-");
}

function nextAction(pull: PullRequestModel) {
  if (pull.status === "READY") return "Merge decision";
  if (pull.status === "BLOCKED") return pull.blockers[0] ?? "Resolve blocker";
  if (pull.status === "WAITING") return `Resolve upstream #${pull.dependencies[0]}`;
  if (pull.status === "STALE") return "Revalidate stale work";
  return "Human review";
}

function prLayout(pulls: PullRequestModel[]) {
  const byNumber = new Map(pulls.map((pull) => [pull.number, pull]));
  const memo = new Map<number, number>();
  const levelOf = (pull: PullRequestModel, stack = new Set<number>()): number => {
    if (memo.has(pull.number)) return memo.get(pull.number)!;
    if (stack.has(pull.number)) return 0;
    const nextStack = new Set(stack).add(pull.number);
    const level = pull.dependencies.length
      ? Math.max(...pull.dependencies.map((number) => byNumber.get(number)).filter(Boolean).map((parent) => levelOf(parent!, nextStack) + 1), 0)
      : 0;
    memo.set(pull.number, level);
    return level;
  };
  const ordered = [...pulls].sort((a, b) => levelOf(a) - levelOf(b) || a.number - b.number);
  const indexByNumber = new Map(ordered.map((pull, index) => [pull.number, index]));
  const maxLevel = Math.max(0, ...ordered.map((pull) => levelOf(pull)));
  const width = 72 + Math.max(3, maxLevel + 1) * LANE_GAP;
  const points = new Map(ordered.map((pull, index) => [pull.number, { x: LANE_X + levelOf(pull) * LANE_GAP, y: index * ROW_H + ROW_H / 2 }]));
  const edges = ordered.flatMap((pull) => pull.dependencies.map((dependency) => {
    const from = points.get(dependency);
    const to = points.get(pull.number);
    return from && to ? { dependency, pull: pull.number, from, to } : null;
  })).filter(Boolean) as Array<{dependency:number; pull:number; from:{x:number;y:number}; to:{x:number;y:number}}>;
  return { ordered, levelOf, width, points, edges, indexByNumber };
}

function PullFlow({ pulls, selected, onSelect }: { pulls: PullRequestModel[]; selected: number | null; onSelect: (number: number) => void }) {
  const layout = useMemo(() => prLayout(pulls), [pulls]);
  const height = Math.max(ROW_H, layout.ordered.length * ROW_H);
  return <div className="history" data-testid="pr-flow-graph" data-edge-count={layout.edges.length}>
    <div className="graph-rail" style={{ width: layout.width }}>
      <svg width={layout.width} height={height} aria-hidden="true">
        {Array.from({ length: Math.max(3, Math.ceil((layout.width - LANE_X) / LANE_GAP)) }).map((_, lane) => {
          const x = LANE_X + lane * LANE_GAP;
          return <line key={lane} className="lane-guide" x1={x} x2={x} y1={0} y2={height} />;
        })}
        {layout.edges.map((edge) => {
          const mid = (edge.from.y + edge.to.y) / 2;
          return <path key={`${edge.dependency}-${edge.pull}`} className="flow-edge" d={`M ${edge.from.x} ${edge.from.y} C ${edge.from.x} ${mid}, ${edge.to.x} ${mid}, ${edge.to.x} ${edge.to.y}`} />;
        })}
        {layout.ordered.map((pull) => {
          const point = layout.points.get(pull.number)!;
          return <g key={pull.number} className={`graph-dot dot-${statusClass(pull.status)} ${selected === pull.number ? "is-selected" : ""}`}>
            <circle cx={point.x} cy={point.y} r={selected === pull.number ? 8 : 6} />
            <circle className="dot-core" cx={point.x} cy={point.y} r={2.3} />
          </g>;
        })}
      </svg>
    </div>
    <div className="history-rows">
      {layout.ordered.map((pull) => <button key={pull.number} className={`history-row ${selected === pull.number ? "selected" : ""}`} onClick={() => onSelect(pull.number)}>
        <div className="row-main">
          <div className="row-title"><strong>#{pull.number}</strong><span>{pull.title}</span></div>
          <div className="row-sub"><span className="branch-chip">{pull.head}</span><span>→ {pull.base}</span><span>{pull.author}</span></div>
        </div>
        <div className="row-signals">
          {pull.downstreamCount > 0 && <span className="impact">blocks {pull.downstreamCount}</span>}
          <span className={`status s-${statusClass(pull.status)}`}>{statusLabel(pull.status)}</span>
          <span className="action">{nextAction(pull)}</span>
        </div>
      </button>)}
    </div>
  </div>;
}

function CommitHistory({ commits }: { commits: CommitRecord[] }) {
  const visible = commits.slice(0, 80);
  const branches = [...new Set(visible.map((commit) => commit.branch))].slice(0, 8);
  const laneFor = (commit: CommitRecord) => Math.max(0, branches.indexOf(commit.branch));
  const width = 72 + Math.max(3, branches.length) * 24;
  const height = Math.max(ROW_H, visible.length * 58);
  const positions = new Map(visible.map((commit, index) => [commit.sha, { x: 26 + laneFor(commit) * 24, y: index * 58 + 29 }]));
  return <div className="history commit-history" data-testid="commit-graph">
    <div className="graph-rail" style={{ width }}><svg width={width} height={height} aria-hidden="true">
      {branches.map((branch, lane) => <line key={branch} className="lane-guide" x1={26 + lane * 24} x2={26 + lane * 24} y1={0} y2={height} />)}
      {visible.flatMap((commit) => commit.parents.map((parent) => {
        const from = positions.get(commit.sha); const to = positions.get(parent);
        if (!from || !to) return null;
        const mid = (from.y + to.y) / 2;
        return <path key={`${commit.sha}-${parent}`} className="commit-edge" d={`M ${from.x} ${from.y} C ${from.x} ${mid}, ${to.x} ${mid}, ${to.x} ${to.y}`} />;
      }))}
      {visible.map((commit) => { const point = positions.get(commit.sha)!; return <circle key={commit.sha} className="commit-dot" cx={point.x} cy={point.y} r={5} />; })}
    </svg></div>
    <div className="history-rows">{visible.map((commit) => <div key={commit.sha} className="history-row commit-row">
      <code>{commit.sha.slice(0, 7)}</code>
      <div className="row-main"><div className="row-title"><span>{commit.message}</span></div><div className="row-sub"><span className="branch-chip">{commit.branch}</span><span>{commit.author}</span><span>{new Date(commit.timestamp).toLocaleString()}</span></div></div>
      {commit.prNumber && <span className="pr-chip">PR #{commit.prNumber}</span>}
    </div>)}</div>
  </div>;
}

export function App() {
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<"flow" | "commits">("flow");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  useEffect(() => { loadRepositories().then((items) => { setRepos(items); setRepo((current) => current || items[0] || ""); }).catch((reason) => { setError(reason.message); setLoading(false); }); }, []);
  async function refresh(target: string, force = false) { if (!target) return; setLoading(true); setError(null); if (snapshot?.repository !== target) setSnapshot(null); try { setSnapshot(await loadSnapshot(target, force)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Failed to load GitHub data"); } finally { setLoading(false); } }
  useEffect(() => { if (repo) void refresh(repo); }, [repo]);

  const pulls = useMemo(() => snapshot ? buildPullRequestGraph(snapshot.pulls) : [], [snapshot]);
  const filtered = useMemo(() => pulls.filter((pull) => {
    const match = !query || `${pull.number} ${pull.title} ${pull.author} ${pull.head} ${pull.base}`.toLowerCase().includes(query.toLowerCase());
    return match && (statusFilter === "ALL" || pull.status === statusFilter);
  }), [pulls, query, statusFilter]);
  const ranked = useMemo(() => rankBottlenecks(pulls).slice(0, 3), [pulls]);
  const selectedPull = pulls.find((pull) => pull.number === selected) ?? null;
  const counts = Object.fromEntries(STATUS_ORDER.map((status) => [status, pulls.filter((pull) => pull.status === status).length]));

  return <main className="shell" data-testid="dashboard-root">
    <header className="topbar">
      <div className="brand"><GitBranch size={18}/><div><strong>Dev Flow</strong><span>GitHub topology</span></div></div>
      <div className="repo-control"><span>Repository</span><select aria-label="Repository" value={repo} onChange={(event) => { setSelected(null); setRepo(event.target.value); }}>{repos.map((item) => <option key={item}>{item}</option>)}</select></div>
      <button className="refresh" onClick={() => void refresh(repo, true)} disabled={loading}><RefreshCw size={15}/> Refresh</button>
    </header>

    <section className="workspace-head">
      <div><h1>{repo || "Development flow"}</h1><p>Pull requests, dependencies and commit topology in one working view.</p></div>
      <div className="live-meta"><span><span className="live-dot"/> LIVE</span><span><ShieldCheck size={13}/> GitHub {snapshot?.authentication ?? "…"}</span><span>{snapshot ? `${snapshot.cache.hit ? "cache" : "fresh"} · ${new Date(snapshot.fetchedAt).toLocaleTimeString()}` : "loading"}</span></div>
    </section>

    <section className="metric-strip">
      <div className="metric primary"><span>OPEN</span><strong>{pulls.length}</strong></div>
      {STATUS_ORDER.map((status) => <button key={status} className={statusFilter === status ? "active" : ""} onClick={() => { setStatusFilter(statusFilter === status ? "ALL" : status); setTab("flow"); }}><span>{statusLabel(status)}</span><strong>{counts[status] ?? 0}</strong></button>)}
    </section>

    {error && <div className="error" role="alert"><AlertTriangle size={17}/><div><strong>GitHub data unavailable</strong><p>{error}</p></div></div>}
    {loading && !snapshot && <div className="loading">Reading live repository topology…</div>}

    <div className="content-grid">
      <section className="main-panel">
        <div className="panel-bar">
          <div className="segmented"><button className={tab === "flow" ? "active" : ""} onClick={() => setTab("flow")}><GitPullRequest size={14}/> PR Flow</button><button className={tab === "commits" ? "active" : ""} onClick={() => setTab("commits")}><GitCommit size={14}/> Commit Graph</button></div>
          {tab === "flow" && <div className="search"><Search size={14}/><input aria-label="Search PRs" placeholder="Search PR, branch, author…" value={query} onChange={(event) => setQuery(event.target.value)}/></div>}
        </div>
        {snapshot && tab === "flow" && (filtered.length ? <PullFlow pulls={filtered} selected={selected} onSelect={setSelected}/> : <div className="empty">No pull requests match this filter.</div>)}
        {snapshot && tab === "commits" && <CommitHistory commits={snapshot.commits}/>} 
      </section>

      <aside className="side-panel">
        <div className="side-heading"><span>BOTTLENECKS</span><strong>What blocks the graph</strong></div>
        {ranked.length ? ranked.map((pull, index) => <button key={pull.number} className="bottleneck" onClick={() => { setTab("flow"); setSelected(pull.number); }}>
          <span className="b-index">0{index + 1}</span><div><strong>#{pull.number} {pull.title}</strong><p>{nextAction(pull)}</p><span>{pull.downstreamCount} downstream · score {pull.bottleneckScore}</span></div>
        </button>) : <div className="side-empty">No active bottleneck.</div>}
      </aside>
    </div>

    {selectedPull && <div className="backdrop" onClick={() => setSelected(null)}><aside className="drawer" role="dialog" aria-label={`PR #${selectedPull.number} details`} onClick={(event) => event.stopPropagation()}>
      <button className="close" onClick={() => setSelected(null)}>×</button>
      <div className="drawer-kicker"><span className={`status s-${statusClass(selectedPull.status)}`}>{statusLabel(selectedPull.status)}</span><span>PR #{selectedPull.number}</span></div>
      <h2>{selectedPull.title}</h2>
      <a href={selectedPull.url} target="_blank" rel="noreferrer">Open on GitHub <ExternalLink size={13}/></a>
      <div className="route"><span>{selectedPull.head}</span><b>→</b><span>{selectedPull.base}</span></div>
      <dl><dt>Author</dt><dd>{selectedPull.author}</dd><dt>Head SHA</dt><dd><code>{selectedPull.headSha.slice(0, 12)}</code></dd><dt>Commits</dt><dd>{selectedPull.commitCount ?? 0}</dd><dt>Depends on</dt><dd>{selectedPull.dependencies.length ? selectedPull.dependencies.map((n) => `#${n}`).join(", ") : "—"}</dd><dt>Downstream</dt><dd>{selectedPull.downstream.length ? selectedPull.downstream.map((n) => `#${n}`).join(", ") : "—"}</dd></dl>
      <h3>Blocking reason</h3>{selectedPull.blockers.length ? <ul>{selectedPull.blockers.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted">No hard blocker detected.</p>}
      <h3>Checks</h3><ul className="check-list">{selectedPull.checks.length ? selectedPull.checks.map((check) => <li key={`${check.name}-${check.url}`}><span>{check.name}</span><strong>{check.status === "completed" ? check.conclusion ?? "completed" : check.status}</strong></li>) : <li>No check-runs reported.</li>}</ul>
    </aside></div>}
  </main>;
}
