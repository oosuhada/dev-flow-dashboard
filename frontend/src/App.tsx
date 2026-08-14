import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  Bell,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Code2,
  ExternalLink,
  FileCode2,
  File,
  Files,
  Folder,
  FolderOpen,
  GitBranch,
  GitCommit,
  GitMerge,
  GitPullRequest,
  MessageSquare,
  Moon,
  Sparkles,
  RefreshCw,
  Search,
  Sun,
  Tag,
  X,
} from "lucide-react";
import {
  askAIProject,
  loadAICommit,
  loadAIProject,
  loadAIProjectHistory,
  loadAIProjectHistoryDetail,
  loadActivity,
  loadCommit,
  loadFile,
  loadPull,
  loadRepositories,
  loadSnapshot,
  type AICommitAnalysis,
  type AIChatMessage,
  type AIProjectHistoryItem,
  type AIProjectState,
  type ActivityItem,
  type CommitDetail,
  type CommitFile,
  type CommitRecord,
  type FileContent,
  type PullDetail,
  type PullReviewComment,
  type Snapshot,
} from "./api";
import { buildPullRequestGraph, type PullRelation, type PullRequestInput, type PullRequestModel } from "./graphModel";
import { GRAPH_PADDING, ROW_HEIGHT, VERTEX_RADIUS } from "./gitgraph/constants";
import { computeGraphLayout } from "./gitgraph/layout";
import { buildFileTree, type FileTreeNode } from "./gitgraph/fileTree";
import { branchStrokes } from "./gitgraph/strokes";
import { graphHeight, graphWidth, laneX, rowY } from "./gitgraph/utils";

const GRAPH_COLORS = [
  "#0085d9", "#d9008f", "#00d90a", "#d98500", "#a300d9", "#ff0000",
  "#00d9cc", "#e138e8", "#85d900", "#dc5b23", "#6f24d6", "#ffcc00",
];
const shortDate = (value: string) => new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
}).format(new Date(value));
const shortTime = (value: string) => new Intl.DateTimeFormat(undefined, {
  hour: "2-digit", minute: "2-digit",
}).format(new Date(value));

function laneColor(lane: number) {
  return GRAPH_COLORS[lane % GRAPH_COLORS.length];
}

function relativeAge(value: string) {
  const ageMs = Math.max(0, Date.now() - new Date(value).getTime());
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 60) return `${Math.max(1, minutes)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

const ACTIVITY_BUCKET_ORDER = ["1h", "2h", "3h", "6h", "12h", "1d", "2d", "3d", "1w", "older"] as const;
type ActivityBucket = typeof ACTIVITY_BUCKET_ORDER[number];

function activityBucket(value: string): ActivityBucket | null {
  const ageMs = Math.max(0, Date.now() - new Date(value).getTime());
  const hour = 60 * 60 * 1000;
  const day = 24 * hour;
  if (ageMs < hour) return null;
  if (ageMs < 2 * hour) return "1h";
  if (ageMs < 3 * hour) return "2h";
  if (ageMs < 6 * hour) return "3h";
  if (ageMs < 12 * hour) return "6h";
  if (ageMs < day) return "12h";
  if (ageMs < 2 * day) return "1d";
  if (ageMs < 3 * day) return "2d";
  if (ageMs < 7 * day) return "3d";
  if (ageMs < 14 * day) return "1w";
  return "older";
}

function CommitGraph({ commits, headSha, defaultBranch, selected, onSelect, query, columnWidths, onColumnWidths }: {
  commits: CommitRecord[];
  headSha: string | null;
  defaultBranch: string;
  selected: string | null;
  onSelect: (commit: CommitRecord) => void;
  query: string;
  columnWidths: [number, number, number, number, number];
  onColumnWidths: (value: [number, number, number, number, number]) => void;
}) {
  const visible = useMemo(() => commits.slice(0, 100), [commits]);
  const head = headSha ?? visible[0]?.sha ?? null;
  const layout = useMemo(() => computeGraphLayout(visible, head), [visible, head]);
  const strokes = useMemo(() => layout.branches.flatMap((branch) => branchStrokes(branch, false)), [layout]);
  const layoutWidth = Math.max(64, graphWidth(layout) + GRAPH_PADDING);
  const width = Math.max(layoutWidth, columnWidths[0]);
  const height = Math.max(ROW_HEIGHT, graphHeight(layout));
  const needle = query.trim().toLowerCase();
  const effectiveWidths: [number, number, number, number, number] = [width, ...columnWidths.slice(1)] as [number, number, number, number, number];
  const headerTemplate = effectiveWidths.map((value) => `${value}px`).join(" ");
  const rowTemplate = effectiveWidths.slice(1).map((value) => `${value}px`).join(" ");
  const tableWidth = effectiveWidths.reduce((sum, value) => sum + value, 0);
  const minWidths = [layoutWidth, 260, 110, 100, 76];
  function startColumnResize(index: number, startX: number) {
    const startWidth = effectiveWidths[index];
    const move = (event: PointerEvent) => {
      const next = [...effectiveWidths] as [number, number, number, number, number];
      next[index] = Math.max(minWidths[index], startWidth + event.clientX - startX);
      onColumnWidths(next);
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); document.body.classList.remove("is-resizing"); };
    document.body.classList.add("is-resizing");
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }
  const headers = ["Graph", "Description", "Date", "Author", "Commit"];

  return <div className="commit-table" data-testid="commit-graph" style={{ minWidth: `${tableWidth}px`, width: `${tableWidth}px` }}>
    <div className="commit-table-head" style={{ gridTemplateColumns: headerTemplate }}>
      {headers.map((label, index) => <span key={label} className="commit-column-head">{label}<i className="column-resizer" onPointerDown={(event) => { event.preventDefault(); startColumnResize(index, event.clientX); }}/></span>)}
    </div>
    <div className="commit-table-body">
      <div className="commit-svg" style={{ width }}>
        <svg width={width} height={height} aria-hidden="true">
          {strokes.map((stroke, index) => <g key={index}>
            <path d={stroke.path} className="git-edge-shadow"/>
            <path d={stroke.path} stroke={laneColor(stroke.colour)} className="git-edge"/>
          </g>)}
          {visible.map((commit, index) => {
            const vertex = layout.vertices[index];
            if (!vertex) return null;
            const active = selected === commit.sha;
            const color = laneColor(vertex.colour);
            return <g key={commit.sha}>
              {active && <circle cx={laneX(vertex.x)} cy={rowY(vertex.y)} r={VERTEX_RADIUS + 4} fill={color} opacity=".18"/>}
              <circle
                cx={laneX(vertex.x)} cy={rowY(vertex.y)} r={VERTEX_RADIUS}
                fill={vertex.isCurrent ? "#1e1e1e" : color}
                stroke={color}
                strokeWidth={vertex.isCurrent || active ? 2.5 : 1}
              />
            </g>;
          })}
        </svg>
      </div>
      <div className="commit-rows" style={{ marginLeft: width, width: `${tableWidth - width}px`, minWidth: `${tableWidth - width}px` }}>
        {visible.map((commit, index) => {
          const colour = layout.vertices[index]?.colour ?? 0;
          const orderedRefs = [...commit.refs].sort((a, b) => Number(b.type === "head" && b.name === defaultBranch) - Number(a.type === "head" && a.name === defaultBranch));
          const haystack = `${commit.sha} ${commit.message} ${commit.author} ${commit.refs.map((ref) => ref.name).join(" ")} ${commit.prNumber ?? ""}`.toLowerCase();
          const filteredOut = needle !== "" && !haystack.includes(needle);
          return <button key={commit.sha} style={{ gridTemplateColumns: rowTemplate }} className={`commit-row ${selected === commit.sha ? "selected" : ""} ${filteredOut ? "filtered-out" : ""}`} onClick={() => onSelect(commit)}>
            <div className="commit-description">
              {head === commit.sha && <span className="head-marker" style={{ borderColor: laneColor(colour) }} aria-label="HEAD"/>}
              {orderedRefs.map((ref) => <span key={`${ref.type}-${ref.name}`} className={`ref-pill ref-${ref.type}`} style={{ borderColor: ref.type === "head" && ref.name === defaultBranch ? laneColor(colour) : undefined }} title={`${ref.type}: ${ref.name}`}>
                {ref.type === "tag" ? <Tag size={10}/> : <GitBranch size={10}/>}<span>{ref.name}</span>
              </span>)}
              <span className="commit-message">{commit.message}</span>
              {commit.prNumber && <span className="pr-ref"><GitPullRequest size={10}/>#{commit.prNumber}</span>}
            </div>
            <time>{shortDate(commit.timestamp)}</time>
            <span className="commit-author" title={commit.email ? `${commit.author} <${commit.email}>` : commit.author}>{commit.author}</span>
            <code>{commit.sha.slice(0, 7)}</code>
          </button>;
        })}
      </div>
    </div>
  </div>;
}

function DiffView({ file }: { file: CommitFile }) {
  if (!file.patch) return <div className="no-preview">GitHub did not return a text patch for this file. Use <strong>File</strong> to inspect the current contents.</div>;
  return <pre className="diff-view">{file.patch.split("\n").map((line, index) => {
    const kind = line.startsWith("+") && !line.startsWith("+++") ? "add"
      : line.startsWith("-") && !line.startsWith("---") ? "del"
      : line.startsWith("@@") ? "hunk" : "ctx";
    return <div key={index} className={`diff-line ${kind}`}><span>{index + 1}</span><code>{line || " "}</code></div>;
  })}</pre>;
}

function CodeView({ content }: { content: FileContent | null }) {
  if (!content) return <div className="no-preview">Loading file…</div>;
  return <div className="code-wrap">
    {(content.binary || content.truncated) && <div className="code-note">{content.binary ? "Binary/non UTF-8 content · " : ""}{content.truncated ? "preview truncated to 750 KB" : ""}</div>}
    <pre className="code-view">{content.content.split("\n").map((line, index) => <div key={index} className="code-line"><span>{index + 1}</span><code>{line || " "}</code></div>)}</pre>
  </div>;
}

function ChangedFileTree({ files, selectedFile, onFile }: { files: CommitFile[]; selectedFile: string | null; onFile: (file: CommitFile) => void }) {
  const tree = useMemo(() => buildFileTree(files), [files]);
  const [closed, setClosed] = useState<Set<string>>(() => new Set());
  useEffect(() => setClosed(new Set()), [files]);

  function render(nodes: FileTreeNode[], depth = 0): ReactNode {
    return nodes.map((node) => {
      if (node.type === "folder") {
        const open = !closed.has(node.path);
        return <div key={node.path}>
          <button className="tree-entry folder-entry" style={{ paddingLeft: 7 + depth * 13 }} onClick={() => setClosed((current) => {
            const next = new Set(current); if (!next.delete(node.path)) next.add(node.path); return next;
          })}>{open ? <FolderOpen size={12}/> : <Folder size={12}/>}<span>{node.name}</span></button>
          {open && render(node.children, depth + 1)}
        </div>;
      }
      const file = node.file;
      return <button key={file.filename} className={`tree-entry file-entry ${selectedFile === file.filename ? "active" : ""}`} style={{ paddingLeft: 7 + depth * 13 }} onClick={() => onFile(file)} title={file.filename}>
        <File size={11}/><span className={`file-status status-${file.status}`}>{file.status.slice(0, 1).toUpperCase()}</span><span className="file-path">{node.name}</span><span className="file-delta"><b>+{file.additions}</b><i>−{file.deletions}</i></span>
      </button>;
    });
  }
  return <>{render(tree)}</>;
}

function CommitInspector({ repo, detail, loading, selectedFile, onFile, fileContent, fileLoading, mode, setMode, ai, aiLoading, onPullSelect, onClose }: {
  repo: string;
  detail: CommitDetail | null;
  loading: boolean;
  selectedFile: string | null;
  onFile: (file: CommitFile) => void;
  fileContent: FileContent | null;
  fileLoading: boolean;
  mode: "diff" | "file";
  setMode: (mode: "diff" | "file") => void;
  ai: AICommitAnalysis | null;
  aiLoading: boolean;
  onPullSelect: (number: number) => void;
  onClose: () => void;
}) {
  if (loading || !detail) return <aside className="commit-inspector"><div className="inspector-loading">Loading commit…</div></aside>;
  const current = detail.files.find((file) => file.filename === selectedFile) ?? detail.files[0] ?? null;
  return <aside className="commit-inspector">
    <div className="inspector-titlebar"><div><GitCommit size={14}/><code>{detail.sha.slice(0, 10)}</code><span>{repo}</span></div><button onClick={onClose} aria-label="Close commit detail"><X size={15}/></button></div>
    <div className="commit-summary">
      <div className="commit-subject">{detail.message.split("\n", 1)[0]}</div>
      <div className="commit-meta"><strong>{detail.author}</strong><span>{detail.authoredAt ? new Date(detail.authoredAt).toLocaleString() : ""}</span><a href={detail.htmlUrl} target="_blank" rel="noreferrer">GitHub <ExternalLink size={11}/></a></div>
      <div className="commit-stats"><span>{detail.files.length} files</span><b className="plus">+{detail.stats.additions}</b><b className="minus">−{detail.stats.deletions}</b>{detail.parents.map((parent) => <code key={parent}>parent {parent.slice(0, 7)}</code>)}</div>
      <div className={`commit-ai ${aiLoading ? "loading" : ""}`}>
        <div className="commit-ai-head"><Sparkles size={12}/><strong>Gemini Flow Analysis</strong>{ai?.riskLevel && <span className={`ai-risk risk-${ai.riskLevel.toLowerCase()}`}>{ai.riskLevel}</span>}</div>
        {aiLoading && !ai ? <span className="ai-muted">현재 PR 흐름과 이 커밋의 영향을 분석하는 중…</span> : ai?.status === "ready" ? <>
          <p>{ai.summary}</p>
          {ai.whyItMatters && <div className="ai-why"><strong>Why it matters</strong><span>{ai.whyItMatters}</span></div>}
          {ai.reviewFocus?.length ? <div className="ai-review-focus"><strong>Review focus</strong>{ai.reviewFocus.slice(0, 4).map((item) => <span key={item}>• {item}</span>)}</div> : null}
          {ai.recommendedNextStep && <div className="ai-next"><strong>Next</strong><span>{ai.recommendedNextStep}</span></div>}
          {ai.relatedPulls?.length ? <div className="ai-related"><strong>Related PR</strong>{ai.relatedPulls.map((number) => <button key={number} onClick={() => onPullSelect(number)}>#{number}</button>)}</div> : null}
        </> : <span className="ai-muted">AI analysis unavailable.</span>}
      </div>
    </div>
    <div className="inspector-workspace">
      <div className="changed-files">
        <div className="files-title"><Files size={13}/> Changes <span>{detail.files.length}</span></div>
        <ChangedFileTree files={detail.files} selectedFile={current?.filename ?? null} onFile={onFile}/>
      </div>
      <div className="file-preview">
        {current ? <>
          <div className="file-preview-bar"><div><FileCode2 size={13}/><span>{current.filename}</span></div><div className="view-tabs"><button className={mode === "diff" ? "active" : ""} onClick={() => setMode("diff")}>Diff</button><button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}><Code2 size={12}/> File</button>{current.blobUrl && <a href={current.blobUrl} target="_blank" rel="noreferrer"><ExternalLink size={12}/></a>}</div></div>
          {mode === "diff"
            ? <DiffView file={current}/>
            : fileLoading
              ? <div className="no-preview">Loading file contents…</div>
              : <CodeView content={fileContent}/>
          }
        </> : <div className="no-preview">This commit has no file changes.</div>}
      </div>
    </div>
  </aside>;
}

function statusLabel(status: PullRequestModel["status"]) { return status === "NEEDS_REVIEW" ? "NEEDS REVIEW" : status; }
function statusClass(status: PullRequestModel["status"]) { return status.toLowerCase().replace("_", "-"); }

function reviewSignals(pull: PullRequestInput) {
  const human = pull.reviews.filter((review) => !review.isBot && !review.user.endsWith("[bot]"));
  const bots = pull.reviews.filter((review) => review.isBot || review.user.endsWith("[bot]"));
  const latestHuman = new Map<string, (typeof human)[number]>();
  for (const review of human) latestHuman.set(review.user, review);
  const states = [...latestHuman.values()].map((review) => review.state.toUpperCase());
  const humanLabel = states.includes("CHANGES_REQUESTED")
    ? "CHANGES"
    : states.includes("APPROVED")
      ? "APPROVED"
      : "PENDING";
  const latestBot = bots.at(-1);
  const body = (latestBot?.body ?? "").toLowerCase();
  const autoLabel = !latestBot
    ? "—"
    : /not ready|\[p[01]\]|unresolved|issues? found/.test(body)
      ? "ISSUES"
      : "REVIEWED";
  return { humanLabel, autoLabel };
}

function lifecycleLabel(pull: { lifecycle?: "open" | "merged" | "closed"; draft: boolean }) {
  if (pull.lifecycle === "merged") return "MERGED";
  if (pull.lifecycle === "closed") return "CLOSED";
  return pull.draft ? "DRAFT" : "OPEN";
}

type PullSort = "ai" | "flow" | "updated" | "number";

function orderPulls(pulls: PullRequestInput[], relations: PullRelation[], sort: PullSort, aiRanks = new Map<number, number>()) {
  if (sort === "updated") {
    return [...pulls].sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime() || b.number - a.number);
  }
  if (sort === "number") return [...pulls].sort((a, b) => b.number - a.number);

  const byNumber = new Map(pulls.map((pull) => [pull.number, pull]));
  const visible = new Set(byNumber.keys());
  const outgoing = new Map<number, number[]>();
  const incoming = new Map<number, number[]>();
  const undirected = new Map<number, number[]>();
  for (const edge of relations) {
    if (!visible.has(edge.source) || !visible.has(edge.target)) continue;
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
    incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge.source]);
    undirected.set(edge.source, [...(undirected.get(edge.source) ?? []), edge.target]);
    undirected.set(edge.target, [...(undirected.get(edge.target) ?? []), edge.source]);
  }

  const seen = new Set<number>();
  const components: PullRequestInput[][] = [];
  for (const pull of pulls) {
    if (seen.has(pull.number)) continue;
    const numbers: number[] = [];
    const queue = [pull.number];
    while (queue.length) {
      const current = queue.shift()!;
      if (seen.has(current)) continue;
      seen.add(current); numbers.push(current);
      queue.push(...(undirected.get(current) ?? []));
    }

    const inComponent = new Set(numbers);
    const indegree = new Map(numbers.map((number) => [
      number,
      (incoming.get(number) ?? []).filter((source) => inComponent.has(source)).length,
    ]));
    const ready = numbers.filter((number) => (indegree.get(number) ?? 0) === 0);
    const ordered: PullRequestInput[] = [];
    const emitted = new Set<number>();
    while (ready.length) {
      ready.sort((a, b) => {
        if (sort === "ai") {
          const rankDiff = (aiRanks.get(a) ?? 9999) - (aiRanks.get(b) ?? 9999);
          if (rankDiff) return rankDiff;
        }
        return new Date(byNumber.get(b)!.updatedAt).getTime() - new Date(byNumber.get(a)!.updatedAt).getTime() || a - b;
      });
      const number = ready.shift()!;
      if (emitted.has(number)) continue;
      emitted.add(number); ordered.push(byNumber.get(number)!);
      for (const child of outgoing.get(number) ?? []) {
        if (!inComponent.has(child)) continue;
        indegree.set(child, (indegree.get(child) ?? 1) - 1);
        if ((indegree.get(child) ?? 0) === 0) ready.push(child);
      }
    }
    for (const number of numbers) if (!emitted.has(number)) ordered.push(byNumber.get(number)!);
    components.push(ordered);
  }

  components.sort((a, b) => {
    if (sort === "ai") {
      const aRank = Math.min(...a.map((pull) => aiRanks.get(pull.number) ?? 9999));
      const bRank = Math.min(...b.map((pull) => aiRanks.get(pull.number) ?? 9999));
      if (aRank !== bRank) return aRank - bRank;
    }
    const aLatest = Math.max(...a.map((pull) => new Date(pull.updatedAt).getTime()));
    const bLatest = Math.max(...b.map((pull) => new Date(pull.updatedAt).getTime()));
    return bLatest - aLatest || Math.max(...b.map((pull) => pull.number)) - Math.max(...a.map((pull) => pull.number));
  });
  return components.flat();
}

function PullRequestGraph({ pulls, relations, flowByNumber, selected, onSelect, sort, aiRanks }: {
  pulls: PullRequestInput[];
  relations: PullRelation[];
  flowByNumber: Map<number, PullRequestModel>;
  selected: number | null;
  onSelect: (value: number) => void;
  sort: PullSort;
  aiRanks: Map<number, number>;
}) {
  const ordered = useMemo(() => orderPulls(pulls, relations, sort, aiRanks), [pulls, relations, sort, aiRanks]);
  const visibleNumbers = useMemo(() => new Set(ordered.map((pull) => pull.number)), [ordered]);
  const visibleRelations = useMemo(() => relations.filter((edge) => visibleNumbers.has(edge.source) && visibleNumbers.has(edge.target)), [relations, visibleNumbers]);
  const upstream = new Map<number, number[]>();
  const downstream = new Map<number, number[]>();
  for (const edge of visibleRelations) {
    upstream.set(edge.target, [...(upstream.get(edge.target) ?? []), edge.source]);
    downstream.set(edge.source, [...(downstream.get(edge.source) ?? []), edge.target]);
  }
  const depth = new Map<number, number>();
  for (const pull of ordered) {
    const parents = upstream.get(pull.number) ?? [];
    depth.set(pull.number, parents.length ? Math.max(...parents.map((parent) => (depth.get(parent) ?? 0) + 1)) : 0);
  }
  const parent = new Map(ordered.map((pull) => [pull.number, pull.number]));
  const find = (number: number): number => {
    const current = parent.get(number) ?? number;
    if (current === number) return number;
    const root = find(current); parent.set(number, root); return root;
  };
  for (const edge of visibleRelations) {
    const a = find(edge.source); const b = find(edge.target);
    if (a !== b) parent.set(b, a);
  }
  const componentColour = new Map<number, number>();
  let nextColour = 0;
  for (const pull of ordered) {
    const root = find(pull.number);
    if (!componentColour.has(root)) componentColour.set(root, nextColour++);
  }
  const PR_ROW_HEIGHT = 42;
  const PR_LANE_WIDTH = 22;
  const PR_LANE_OFFSET = 18;
  const maxDepth = Math.max(0, ...ordered.map((pull) => depth.get(pull.number) ?? 0));
  const width = Math.max(58, PR_LANE_OFFSET * 2 + (maxDepth + 1) * PR_LANE_WIDTH);
  const height = Math.max(PR_ROW_HEIGHT, ordered.length * PR_ROW_HEIGHT);
  const points = new Map(ordered.map((pull, index) => [pull.number, {
    x: PR_LANE_OFFSET + (depth.get(pull.number) ?? 0) * PR_LANE_WIDTH,
    y: index * PR_ROW_HEIGHT + PR_ROW_HEIGHT / 2,
  }]));
  return <div className="pr-graph-table" data-testid="pr-graph" data-edge-count={visibleRelations.length}>
    <div className="pr-graph-head" style={{ gridTemplateColumns: `${width}px minmax(330px,1fr) 130px minmax(190px,.5fr) 132px 110px` }}>
      <span>Graph</span><span>Pull request</span><span>Author</span><span>Branch</span><span>Review / State</span><span>Updated</span>
    </div>
    <div className="pr-graph-body">
      <div className="pr-graph-svg" style={{ width }}><svg width={width} height={height} aria-hidden="true">
        {visibleRelations.map((edge) => {
          const from = points.get(edge.source); const to = points.get(edge.target);
          if (!from || !to) return null;
          const root = find(edge.source); const color = laneColor(componentColour.get(root) ?? 0);
          const mid = (from.y + to.y) / 2;
          const path = from.x === to.x
            ? `M ${from.x} ${from.y} L ${to.x} ${to.y}`
            : `M ${from.x} ${from.y} C ${from.x} ${mid}, ${to.x} ${mid}, ${to.x} ${to.y}`;
          return <g key={`${edge.source}-${edge.target}`}><path d={path} className="git-edge-shadow"/><path d={path} stroke={color} className="git-edge"/></g>;
        })}
        {ordered.map((pull) => {
          const point = points.get(pull.number)!;
          const root = find(pull.number); const color = laneColor(componentColour.get(root) ?? 0); const active = selected === pull.number;
          const connected = (upstream.get(pull.number)?.length ?? 0) + (downstream.get(pull.number)?.length ?? 0) > 0;
          return <g key={pull.number}>{active && <circle cx={point.x} cy={point.y} r={VERTEX_RADIUS + 4} fill={color} opacity=".2"/>}<circle cx={point.x} cy={point.y} r={VERTEX_RADIUS} fill={connected ? color : "#777"} stroke="#1e1e1e" strokeWidth={active ? 2.5 : 1}/></g>;
        })}
      </svg></div>
      <div className="pr-graph-rows" style={{ marginLeft: width }}>
        {ordered.map((pull) => {
          const flow = flowByNumber.get(pull.number);
          const deps = upstream.get(pull.number) ?? [];
          const blocks = downstream.get(pull.number) ?? [];
          const reviews = reviewSignals(pull);
          return <button key={pull.number} className={`pr-graph-row ${selected === pull.number ? "selected" : ""}`} onClick={() => onSelect(pull.number)}>
            <div className="pr-graph-title"><span className="pr-number">#{pull.number}</span>{aiRanks.has(pull.number) && <span className="ai-row-rank">AI {aiRanks.get(pull.number)}</span>}<span className="pr-title-text">{pull.title}</span>{deps.length > 0 && <span className="relation-chip">after {deps.map((n) => `#${n}`).join(", ")}</span>}{blocks.length > 0 && <span className="relation-chip impact">blocks {blocks.map((n) => `#${n}`).join(", ")}</span>}</div>
            <span className="pr-author">{pull.author}</span>
            <span className="pr-branch">{pull.head}<ChevronRight size={11}/>{pull.base}</span>
            <span className="pr-review-state"><span className={`lifecycle lifecycle-${pull.lifecycle ?? "open"}`}>{lifecycleLabel(pull)}</span>{flow && <><small className={`auto-review auto-${reviews.autoLabel.toLowerCase()}`}>AUTO {reviews.autoLabel}</small><small className={`human-review human-${reviews.humanLabel.toLowerCase()}`}>HUMAN {reviews.humanLabel}</small></>}</span>
            <time>{shortDate(pull.updatedAt)}</time>
          </button>;
        })}
      </div>
    </div>
  </div>;
}

function PullRequestList({ pulls, relations, flowByNumber, selected, onSelect, sort, aiRanks, columnWidths, onColumnWidths }: {
  pulls: PullRequestInput[];
  relations: PullRelation[];
  flowByNumber: Map<number, PullRequestModel>;
  selected: number | null;
  onSelect: (value: number) => void;
  sort: PullSort;
  aiRanks: Map<number, number>;
  columnWidths: [number, number, number, number, number, number];
  onColumnWidths: (value: [number, number, number, number, number, number]) => void;
}) {
  const visibleNumbers = new Set(pulls.map((pull) => pull.number));
  const upstream = new Map<number, number[]>();
  const downstream = new Map<number, number[]>();
  for (const edge of relations) {
    if (!visibleNumbers.has(edge.source) && !visibleNumbers.has(edge.target)) continue;
    upstream.set(edge.target, [...(upstream.get(edge.target) ?? []), edge.source]);
    downstream.set(edge.source, [...(downstream.get(edge.source) ?? []), edge.target]);
  }
  const ordered = orderPulls(pulls, relations, sort, aiRanks);
  const template = columnWidths.map((width) => `${width}px`).join(" ");
  const minWidths = [260, 90, 140, 100, 160, 90];
  function startColumnResize(index: number, startX: number) {
    const startWidth = columnWidths[index];
    const move = (event: PointerEvent) => {
      const next = [...columnWidths] as [number, number, number, number, number, number];
      next[index] = Math.max(minWidths[index], startWidth + event.clientX - startX);
      onColumnWidths(next);
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }
  const headers = ["Pull request", "Author", "Branch", "Relations", "Review / State", "Updated"];
  return <div className="pr-list" data-testid="pr-list" style={{ minWidth: `${columnWidths.reduce((sum, width) => sum + width, 0)}px` }}>
    <div className="pr-list-head" style={{ gridTemplateColumns: template }}>{headers.map((label, index) => <span key={label} className="pr-column-head">{label}{index < headers.length - 1 && <i className="column-resizer" onPointerDown={(event) => { event.preventDefault(); startColumnResize(index, event.clientX); }}/>}</span>)}</div>
    {ordered.map((pull) => {
      const deps = upstream.get(pull.number) ?? [];
      const blocks = downstream.get(pull.number) ?? [];
      const flow = flowByNumber.get(pull.number);
      const reviews = reviewSignals(pull);
      return <button key={pull.number} style={{ gridTemplateColumns: template }} className={`pr-list-row ${selected === pull.number ? "selected" : ""}`} onClick={() => onSelect(pull.number)}>
        <div className="pr-graph-title"><span className="pr-number">#{pull.number}</span>{aiRanks.has(pull.number) && <span className="ai-row-rank">AI {aiRanks.get(pull.number)}</span>}<span className="pr-title-text">{pull.title}</span></div>
        <span className="pr-author">{pull.author}</span>
        <span className="pr-branch">{pull.head}<ChevronRight size={11}/>{pull.base}</span>
        <span className="pr-relations">{deps.length > 0 && <span>after {deps.map((n) => `#${n}`).join(", ")}</span>}{blocks.length > 0 && <span>blocks {blocks.map((n) => `#${n}`).join(", ")}</span>}{deps.length === 0 && blocks.length === 0 && <i>—</i>}</span>
        <span className="pr-review-state"><span className={`lifecycle lifecycle-${pull.lifecycle ?? "open"}`}>{lifecycleLabel(pull)}</span>{flow && <><small className={`auto-review auto-${reviews.autoLabel.toLowerCase()}`}>AUTO {reviews.autoLabel}</small><small className={`human-review human-${reviews.humanLabel.toLowerCase()}`}>HUMAN {reviews.humanLabel}</small></>}</span>
        <time>{shortDate(pull.updatedAt)}</time>
      </button>;
    })}
  </div>;
}

function AIProjectSidebar({ state, loading, currentRepo, activity, unreadCount, onRefresh, onSelectPull, onMarkActivityRead, onRefreshActivity }: {
  state: AIProjectState | null;
  loading: boolean;
  currentRepo: string;
  activity: ActivityItem[];
  unreadCount: number;
  onRefresh: () => void;
  onSelectPull: (repository: string, number: number) => void;
  onMarkActivityRead: () => void;
  onRefreshActivity: () => void;
}) {
  const [tab, setTab] = useState<"pm" | "chat" | "activity">("pm");
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [messages, setMessages] = useState<AIChatMessage[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>(["그 다음에 뭐할까?", "지금 가장 큰 병목은 뭐야?", "각 팀원이 지금 해야 할 일 알려줘"]);
  const [history, setHistory] = useState<AIProjectHistoryItem[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<number | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<AIProjectState | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [collapsedBuckets, setCollapsedBuckets] = useState<Set<string>>(() => new Set(["older"]));

  const groupedActivity = useMemo(() => {
    const groups = ACTIVITY_BUCKET_ORDER.reduce((result, bucket) => {
      result[bucket] = [];
      return result;
    }, {} as Record<ActivityBucket, ActivityItem[]>);
    for (const item of activity) {
      const bucket = activityBucket(item.createdAt);
      if (bucket) groups[bucket].push(item);
    }
    return groups;
  }, [activity]);
  const recentActivity = useMemo(() => activity.filter((item) => activityBucket(item.createdAt) === null), [activity]);

  function selectTab(value: "pm" | "chat" | "activity") {
    setTab(value);
    if (value === "activity") onMarkActivityRead();
  }

  useEffect(() => {
    if (tab === "activity" && activity.length > 0) onMarkActivityRead();
  }, [tab, activity[0]?.id]);

  useEffect(() => {
    setHistoryLoading(true);
    void loadAIProjectHistory(100).then(setHistory).catch(() => undefined).finally(() => setHistoryLoading(false));
  }, [state?.generatedAt]);

  async function selectSnapshot(id: number | null) {
    setSelectedSnapshotId(id);
    if (id === null) {
      setSelectedSnapshot(null);
      return;
    }
    setSelectedSnapshot(null);
    setHistoryLoading(true);
    try { setSelectedSnapshot(await loadAIProjectHistoryDetail(id)); }
    catch { setSelectedSnapshotId(null); setSelectedSnapshot(null); }
    finally { setHistoryLoading(false); }
  }

  function activityRow(item: ActivityItem) {
    return <button key={item.id} className={`activity-item source-${item.source}`} onClick={() => item.number ? onSelectPull(item.repository, item.number) : undefined} title={new Date(item.createdAt).toLocaleString()}>
      <span className="activity-source-dot"/>
      <div><header><strong>{item.title}</strong><time>{relativeAge(item.createdAt)}</time></header><p>{item.summary || `${item.event}${item.action ? ` · ${item.action}` : ""}`}</p><footer><span>{item.repository.split("/").at(-1)}</span>{item.actor && <span>@{item.actor}</span>}<time>{shortDate(item.createdAt)}</time></footer></div>
    </button>;
  }

  function toggleBucket(bucket: string) {
    setCollapsedBuckets((current) => {
      const next = new Set(current);
      if (!next.delete(bucket)) next.add(bucket);
      return next;
    });
  }

  async function sendChat(question = chatInput) {
    const value = question.trim();
    if (!value || chatLoading) return;
    const history = [...messages];
    setMessages((items) => [...items, { role: "user", content: value }]);
    setChatInput("");
    setChatLoading(true);
    try {
      const response = await askAIProject(value, history);
      setMessages((items) => [...items, { role: "assistant", content: response.answer }]);
      setSuggestions(response.suggestedQuestions ?? []);
    } catch (reason) {
      setMessages((items) => [...items, { role: "assistant", content: reason instanceof Error ? reason.message : "AI PM chat failed" }]);
    } finally { setChatLoading(false); }
  }

  const displayState = selectedSnapshotId === null ? state : selectedSnapshot;
  const judgedAt = displayState?.generatedAt ?? "";
  const historicalItems = history;

  return <aside className="ai-sidebar" data-testid="ai-project-manager">
    <header className="ai-sidebar-head">
      <div><Sparkles size={13}/><strong>AI Project Manager</strong>{loading && <small>re-evaluating…</small>}</div>
      <button onClick={onRefresh} title="Re-evaluate now"><RefreshCw size={12}/></button>
    </header>
    <nav className="ai-sidebar-tabs"><button className={tab === "pm" ? "active" : ""} onClick={() => selectTab("pm")}>AI PM</button><button className={tab === "chat" ? "active" : ""} onClick={() => selectTab("chat")}>Chat</button><button className={tab === "activity" ? "active" : ""} onClick={() => selectTab("activity")}><Bell size={11}/> Activity {unreadCount > 0 && <span className="activity-unread">{unreadCount > 99 ? "99+" : unreadCount}</span>}</button></nav>
    {tab === "pm" ? <div className="pm-timeline">
      {selectedSnapshotId !== null && <div className="pm-history-mode"><span>Historical judgement · snapshot #{selectedSnapshotId}</span><button onClick={() => void selectSnapshot(null)}>Back to live</button></div>}
      <section className="pm-current">
        <div className="pm-current-head"><div><strong>{selectedSnapshotId === null ? "Live judgement" : "Saved judgement"}</strong>{judgedAt && <time>{shortDate(judgedAt)}</time>}</div><button className="pm-history-trigger" onClick={() => setHistoryOpen(true)}>Judgement history <span>{history.length}</span></button></div>
        {selectedSnapshotId !== null && displayState === null ? <div className="pm-snapshot-loading">Loading saved judgement…</div> : <div className="pm-current-body">
          <div className="pm-status-row"><span className={`pm-health health-${(displayState?.projectHealth ?? "loading").toLowerCase()}`}>{displayState?.projectHealth ?? "ANALYZING"}</span>{displayState?.currentStep && <span className="pm-step">Step {displayState.currentStep.number} · {displayState.currentStep.name}</span>}</div>
          <div className="ai-headline"><span>{judgedAt ? shortTime(judgedAt) : "--:--"}</span><strong>{displayState?.headline || "Project Manager가 현재 상태를 읽는 중입니다…"}</strong></div>
          {displayState?.currentObjective && <div className="pm-objective"><strong>Current objective</strong><span>{displayState.currentObjective}</span></div>}
          {displayState?.healthReason && <p className="ai-summary">{displayState.healthReason}</p>}
          <section className="pm-team-list"><h3>Team · judged {judgedAt ? shortTime(judgedAt) : "--:--"}</h3>{(displayState?.teamActions ?? []).map((action) => <article key={action.github} className="pm-team-card">
            <header><strong>{action.name}</strong><code>@{action.github}</code><span>{action.role}</span></header>
            <div className="pm-now"><small>{judgedAt ? shortTime(judgedAt) : "--:--"}</small><strong>{action.now}</strong></div>
            <p>{action.whyNow}</p>
            {action.nextHandoff && <div className="pm-handoff"><span>Handoff</span>{action.nextHandoff}</div>}
            {action.relatedWork?.length ? <div className="pm-related">{action.relatedWork.map((work) => <button key={`${work.repository}-${work.pr}`} onClick={() => onSelectPull(work.repository, work.pr)}>{work.repository === currentRepo ? "PR" : work.repository.split("/").at(-1)} #{work.pr}</button>)}</div> : null}
          </article>)}</section>
          {(displayState?.antiPatternAlerts ?? []).length > 0 && <section className="pm-alerts"><h3>AI PM warnings</h3>{displayState?.antiPatternAlerts?.slice(0, 4).map((alert, index) => <article key={`${alert.title}-${index}`} className={`pm-alert severity-${alert.severity}`}><header><AlertTriangle size={11}/><strong>{alert.title}</strong></header><p>{alert.reason}</p><div><span>Don't</span>{alert.stopDoing}</div><div><span>Do</span>{alert.doInstead}</div></article>)}</section>}
          <section className="pm-queue"><h3>PR queue</h3>{(displayState?.prPriorities ?? []).slice(0, 10).map((item) => <button key={`${item.repository}-${item.number}`} onClick={() => onSelectPull(item.repository, item.number)}><span className="ai-rank">{item.rank}</span><strong>{item.repository.split("/").at(-1)} #{item.number}</strong><span className={`ai-priority priority-${item.priority.toLowerCase()}`}>{item.priority}</span><p>{item.nextAction}</p></button>)}</section>
        </div>}
      </section>
      {historyOpen && <div className="pm-history-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setHistoryOpen(false); }}>
        <section className="pm-history-modal" role="dialog" aria-modal="true" aria-label="Judgement history">
          <header><div><strong>Judgement history</strong><span>{history.length} saved judgements</span></div><button onClick={() => setHistoryOpen(false)} aria-label="Close judgement history"><X size={14}/></button></header>
          <div className="pm-history-modal-body">
            <button className={`pm-history-row live ${selectedSnapshotId === null ? "active" : ""}`} onClick={() => { void selectSnapshot(null); setHistoryOpen(false); }}><div><strong>LIVE</strong><span>Current AI PM judgement</span></div>{state?.generatedAt && <time>{shortDate(state.generatedAt)}</time>}</button>
            {historyLoading && historicalItems.length === 0 && <div className="pm-history-empty">Loading saved judgements…</div>}
            {historicalItems.map((item) => <button key={item.id} className={`pm-history-row ${selectedSnapshotId === item.id ? "active" : ""}`} onClick={() => { void selectSnapshot(item.id); setHistoryOpen(false); }}>
              <div><strong>{shortTime(item.createdAt)}</strong><span>{item.headline || "Saved AI PM judgement"}</span><small>{item.projectHealth ?? "AI PM"}{item.currentStep?.number ? ` · Step ${item.currentStep.number}` : ""}</small></div><time>{shortDate(item.createdAt)}</time>
            </button>)}
          </div>
        </section>
      </div>}
    </div> : tab === "chat" ? <div className="ai-chat">
      <div className="ai-chat-messages">{messages.length === 0 && <div className="ai-chat-empty"><Sparkles size={18}/><strong>프로젝트 맥락을 기억하고 있습니다.</strong><span>다음 작업, 병목, 팀원별 역할, PR 우선순위를 물어보세요.</span></div>}{messages.map((message, index) => <div key={index} className={`ai-chat-message ${message.role}`}><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>)}{chatLoading && <div className="ai-chat-thinking">Gemini가 현재 PM 상태를 확인하는 중…</div>}</div>
      {suggestions.length > 0 && <div className="ai-chat-suggestions">{suggestions.map((suggestion) => <button key={suggestion} onClick={() => void sendChat(suggestion)}>{suggestion}</button>)}</div>}
      <form className="ai-chat-input" onSubmit={(event) => { event.preventDefault(); void sendChat(); }}><textarea value={chatInput} onChange={(event) => setChatInput(event.target.value)} placeholder="그 다음에 뭐할까?" rows={2}/><button disabled={!chatInput.trim() || chatLoading}>Send</button></form>
    </div> : <div className="activity-inbox">
      <div className="activity-toolbar"><div><Bell size={12}/><strong>Live activity</strong><span>{activity.length} stored</span></div><button onClick={onRefreshActivity} title="Refresh activity"><RefreshCw size={11}/></button></div>
      {activity.length === 0 && <div className="activity-empty">GitHub 변화가 들어오면 여기에 누적됩니다.</div>}
      {recentActivity.length > 0 && <div className="activity-items activity-recent">{recentActivity.map(activityRow)}</div>}
      {ACTIVITY_BUCKET_ORDER.map((bucket) => {
        const items = groupedActivity[bucket];
        if (items.length === 0) return null;
        const collapsed = collapsedBuckets.has(bucket);
        const label = bucket === "older" ? "Older" : bucket;
        return <section className="activity-group" key={bucket}>
          <button className="activity-group-head" onClick={() => toggleBucket(bucket)}>{collapsed ? <ChevronRight size={12}/> : <ChevronDown size={12}/>}<strong>{label}</strong><span>{items.length}</span></button>
          {!collapsed && <div className="activity-items">{items.map(activityRow)}</div>}
        </section>;
      })}
    </div>}
  </aside>;
}

function eventText(event: PullDetail["events"][number]) {
  if (event.event === "merged") return "merged this pull request";
  if (event.event === "closed") return "closed this pull request";
  if (event.event === "reopened") return "reopened this pull request";
  if (event.event === "head_ref_force_pushed") return "force-pushed the head branch";
  if (event.event === "ready_for_review") return "marked this pull request ready for review";
  if (event.event === "convert_to_draft") return "converted this pull request to draft";
  if (event.event === "review_requested") return `requested review${event.requestedReviewer ? ` from ${event.requestedReviewer}` : ""}`;
  if (event.event === "labeled") return `added label ${event.label ?? ""}`;
  if (event.event === "unlabeled") return `removed label ${event.label ?? ""}`;
  return event.event.replaceAll("_", " ");
}

function markdownPreview(body: string) {
  return body
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/[`#>*_\[\]()~-]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180);
}

function CollapsibleMarkdownCard({ className, header, body, extra, defaultExpanded = false }: {
  className: string;
  header: ReactNode;
  body: string;
  extra?: ReactNode;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded || body.length <= 180);
  const preview = markdownPreview(body);
  return <article className={className}>
    <header>{header}<button className="collapse-toggle" onClick={() => setExpanded((value) => !value)} title={expanded ? "Collapse" : "Expand"} aria-label={expanded ? "Collapse" : "Expand"}>{expanded ? <ChevronUp size={13}/> : <ChevronDown size={13}/>}</button></header>
    {expanded
      ? <><div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{body || "No description provided."}</ReactMarkdown></div>{extra}</>
      : <button className="collapsed-preview" onClick={() => setExpanded(true)}><span>{preview || "No text content"}</span><small>Expand</small></button>
    }
  </article>;
}

function PullRequestInspector({ repo, detail, loading, tab, setTab, onClose }: {
  repo: string; detail: PullDetail | null; loading: boolean; tab: "conversation" | "commits" | "checks";
  setTab: (tab: "conversation" | "commits" | "checks") => void; onClose: () => void;
}) {
  if (loading || !detail) return <aside className="pr-inspector"><div className="inspector-loading">Loading pull request…</div></aside>;
  const activities = [
    ...detail.comments.map((item) => ({ kind: "comment" as const, at: item.createdAt ?? "", item })),
    ...detail.reviewComments.map((item) => ({ kind: "review-comment" as const, at: item.createdAt ?? "", item })),
    ...detail.reviews.filter((item) => item.body || item.state !== "COMMENTED").map((item) => ({ kind: "review" as const, at: item.submittedAt ?? "", item })),
    ...detail.events.map((item) => ({ kind: "event" as const, at: item.createdAt ?? "", item })),
  ].sort((a, b) => new Date(a.at || 0).getTime() - new Date(b.at || 0).getTime());
  return <aside className="pr-inspector">
    <div className="inspector-titlebar"><div><GitPullRequest size={14}/><strong>PR #{detail.number}</strong><span>{repo}</span></div><button onClick={onClose} aria-label="Close pull request"><X size={15}/></button></div>
    <div className="pr-summary">
      <div className="pr-summary-state"><span className={`lifecycle lifecycle-${detail.lifecycle ?? "open"}`}>{lifecycleLabel(detail)}</span><a href={detail.url} target="_blank" rel="noreferrer">Open on GitHub <ExternalLink size={11}/></a></div>
      <h2>{detail.title}</h2>
      <div className="pr-route"><span>{detail.head}</span><GitMerge size={12}/><span>{detail.base}</span></div>
      <div className="pr-summary-meta"><span>{detail.author}</span><span>{detail.stats.commits} commits</span><span>{detail.stats.changedFiles} files</span><b className="plus">+{detail.stats.additions}</b><b className="minus">−{detail.stats.deletions}</b></div>
    </div>
    <div className="pr-inspector-tabs"><button className={tab === "conversation" ? "active" : ""} onClick={() => setTab("conversation")}><MessageSquare size={12}/>Conversation</button><button className={tab === "commits" ? "active" : ""} onClick={() => setTab("commits")}><GitCommit size={12}/>Commits <span>{detail.commits.length}</span></button><button className={tab === "checks" ? "active" : ""} onClick={() => setTab("checks")}>Checks <span>{detail.checks.length}</span></button></div>
    <div className="pr-inspector-body">
      {tab === "conversation" && <>
        <CollapsibleMarkdownCard className="pr-body-card" body={detail.body || "No description provided."} header={<><strong>{detail.author}</strong><span>opened this pull request · {shortDate(detail.createdAt)}</span></>}/>
        <div className="activity-list">{activities.map((activity, index) => {
          if (activity.kind === "event") return <div key={`event-${activity.item.id}-${index}`} className="activity-event"><span className="activity-dot"/><strong>{activity.item.actor}</strong><span>{eventText(activity.item)}</span><time>{activity.at ? shortDate(activity.at) : ""}</time></div>;
          if (activity.kind === "review") return <CollapsibleMarkdownCard key={`review-${activity.item.id}-${index}`} className="activity-card review-card" body={activity.item.body || activity.item.state.replaceAll("_", " ")} header={<><strong>{activity.item.user}</strong><span className={`review-state review-${activity.item.state.toLowerCase()}`}>{activity.item.state.replaceAll("_", " ")}</span><time>{activity.at ? shortDate(activity.at) : ""}</time></>}/>;
          const comment = activity.item as PullReviewComment;
          return <CollapsibleMarkdownCard key={`${activity.kind}-${comment.id}-${index}`} className="activity-card" body={comment.body} header={<><strong>{comment.user}</strong>{activity.kind === "review-comment" && <span className="path-chip">{comment.path}{comment.line ? `:${comment.line}` : ""}</span>}<time>{activity.at ? shortDate(activity.at) : ""}</time></>} extra={activity.kind === "review-comment" && comment.diffHunk ? <code className="diff-hunk">{comment.diffHunk.split("\n").slice(0, 5).join("\n")}</code> : undefined}/>;
        })}</div>
      </>}
      {tab === "commits" && <div className="pr-commit-list">{detail.commits.map((commit) => <a key={commit.sha} href={commit.url ?? undefined} target="_blank" rel="noreferrer"><span className="commit-node-mini"/><div><strong>{commit.message.split("\n", 1)[0]}</strong><span>{commit.author} · {commit.timestamp ? shortDate(commit.timestamp) : ""}</span></div><code>{commit.sha.slice(0, 7)}</code></a>)}</div>}
      {tab === "checks" && <div className="pr-check-list">{detail.checks.length ? detail.checks.map((check, index) => <a key={`${check.name}-${index}`} href={check.url ?? undefined} target="_blank" rel="noreferrer"><span className={`check-dot check-${check.conclusion ?? check.status}`}/><div><strong>{check.name}</strong><span>{check.status === "completed" ? check.conclusion ?? "completed" : check.status}</span></div></a>) : <div className="no-preview">No check runs reported for this pull request.</div>}</div>}
    </div>
  </aside>;
}

export function App() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const saved = window.localStorage.getItem("dev-flow-theme");
    if (saved === "dark" || saved === "light") return saved;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"commits" | "pulls">("commits");
  const [query, setQuery] = useState("");
  const [selectedCommit, setSelectedCommit] = useState<string | null>(null);
  const [commitDetail, setCommitDetail] = useState<CommitDetail | null>(null);
  const [commitLoading, setCommitLoading] = useState(false);
  const [commitAI, setCommitAI] = useState<AICommitAnalysis | null>(null);
  const [commitAILoading, setCommitAILoading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileMode, setFileMode] = useState<"diff" | "file">("diff");
  const [selectedPull, setSelectedPull] = useState<number | null>(null);
  const [pullDetail, setPullDetail] = useState<PullDetail | null>(null);
  const [pullLoading, setPullLoading] = useState(false);
  const [pullTab, setPullTab] = useState<"conversation" | "commits" | "checks">("conversation");
  const [pullFilter, setPullFilter] = useState<"all" | "open" | "merged" | "closed">("all");
  const [pullSort, setPullSort] = useState<PullSort>("ai");
  const [showPullGraph, setShowPullGraph] = useState(false);
  const [pullGraphScope, setPullGraphScope] = useState<"active" | "filtered">("active");
  const [liveConnected, setLiveConnected] = useState(false);
  const [aiProject, setAIProject] = useState<AIProjectState | null>(null);
  const [aiLoading, setAILoading] = useState(false);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [lastSeenActivityId, setLastSeenActivityId] = useState(() => Number(window.localStorage.getItem("dev-flow-activity-seen")) || 0);
  const [aiPanelOpen, setAIPanelOpen] = useState(() => window.localStorage.getItem("dev-flow-ai-panel") !== "closed");
  const [aiPanelWidth, setAIPanelWidth] = useState(() => Number(window.localStorage.getItem("dev-flow-ai-width")) || 360);
  const [inspectorWidth, setInspectorWidth] = useState(() => Number(window.localStorage.getItem("dev-flow-inspector-width")) || 520);
  const [prColumnWidths, setPrColumnWidths] = useState<[number, number, number, number, number, number]>(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("dev-flow-pr-columns") ?? "null");
      if (Array.isArray(saved) && saved.length === 6 && saved.every((value) => Number.isFinite(value))) return saved as [number, number, number, number, number, number];
    } catch { /* use defaults */ }
    return [420, 120, 220, 160, 220, 110];
  });
  const [commitColumnWidths, setCommitColumnWidths] = useState<[number, number, number, number, number]>(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("dev-flow-commit-columns") ?? "null");
      if (Array.isArray(saved) && saved.length === 5 && saved.every((value) => Number.isFinite(value))) return saved as [number, number, number, number, number];
    } catch { /* use defaults */ }
    return [150, 520, 150, 130, 92];
  });
  const [pendingPull, setPendingPull] = useState<{ repository: string; number: number } | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("dev-flow-theme", theme);
  }, [theme]);

  useEffect(() => { loadRepositories().then((items) => { setRepos(items); setRepo((current) => current || items[0] || ""); }).catch((reason) => { setError(reason.message); setLoading(false); }); }, []);
  async function refresh(target: string, force = false) {
    if (!target) return;
    setLoading(true); setError(null);
    try { setSnapshot(await loadSnapshot(target, force)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Failed to load GitHub data"); }
    finally { setLoading(false); }
  }
  async function refreshAI(force = false) {
    setAILoading(true);
    try { setAIProject(await loadAIProject(force)); }
    catch { /* AI is advisory; GitHub data remains usable. */ }
    finally { setAILoading(false); }
  }
  useEffect(() => { if (repo) { setSelectedCommit(null); setCommitDetail(null); setCommitAI(null); setSelectedFile(null); setSelectedPull(null); setPullDetail(null); void refresh(repo); void refreshAI(); } }, [repo]);

  useEffect(() => { window.localStorage.setItem("dev-flow-ai-panel", aiPanelOpen ? "open" : "closed"); }, [aiPanelOpen]);
  useEffect(() => { window.localStorage.setItem("dev-flow-ai-width", String(aiPanelWidth)); }, [aiPanelWidth]);
  useEffect(() => { window.localStorage.setItem("dev-flow-inspector-width", String(inspectorWidth)); }, [inspectorWidth]);
  useEffect(() => { window.localStorage.setItem("dev-flow-pr-columns", JSON.stringify(prColumnWidths)); }, [prColumnWidths]);
  useEffect(() => { window.localStorage.setItem("dev-flow-commit-columns", JSON.stringify(commitColumnWidths)); }, [commitColumnWidths]);
  useEffect(() => { window.localStorage.setItem("dev-flow-activity-seen", String(lastSeenActivityId)); }, [lastSeenActivityId]);

  async function refreshActivity() {
    try { setActivity(await loadActivity()); }
    catch { /* activity is secondary to the GitHub workbench */ }
  }

  useEffect(() => { void refreshActivity(); }, []);

  useEffect(() => {
    if (!repo) return;
    const params = new URLSearchParams({ repo });
    const source = new EventSource(`/dev_dashboard/api/events?${params}`);
    let timer: ReturnType<typeof setTimeout> | null = null;
    source.onopen = () => setLiveConnected(true);
    source.onerror = () => setLiveConnected(false);
    source.addEventListener("github", (raw) => {
      setAILoading(true);
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        void refresh(repo);
        if (selectedPull) {
          void loadPull(repo, selectedPull).then(setPullDetail).catch(() => undefined);
        }
      }, 350);
    });
    source.addEventListener("ai", () => {
      if (selectedCommit) {
        setCommitAILoading(true);
        void loadAICommit(repo, selectedCommit).then(setCommitAI).catch(() => undefined).finally(() => setCommitAILoading(false));
      }
    });
    source.addEventListener("project", () => {
      void loadAIProject().then((value) => { setAIProject(value); setAILoading(false); }).catch(() => setAILoading(false));
      void refreshActivity();
      if (selectedCommit) {
        setCommitAILoading(true);
        void loadAICommit(repo, selectedCommit).then(setCommitAI).catch(() => undefined).finally(() => setCommitAILoading(false));
      }
    });
    source.addEventListener("activity", () => { void refreshActivity(); });
    const fallback = window.setInterval(() => void refresh(repo), 60_000);
    return () => {
      source.close();
      if (timer) clearTimeout(timer);
      window.clearInterval(fallback);
      setLiveConnected(false);
    };
  }, [repo, selectedPull, selectedCommit]);

  async function inspectCommit(commit: CommitRecord) {
    setSelectedCommit(commit.sha); setCommitLoading(true); setCommitDetail(null); setCommitAI(null); setCommitAILoading(true); setSelectedFile(null); setFileContent(null); setFileMode("diff");
    void loadAICommit(repo, commit.sha).then(setCommitAI).catch(() => undefined).finally(() => setCommitAILoading(false));
    try {
      const detail = await loadCommit(repo, commit.sha);
      setCommitDetail(detail);
      setSelectedFile(detail.files[0]?.filename ?? null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Failed to load commit"); }
    finally { setCommitLoading(false); }
  }

  async function selectFile(file: CommitFile) {
    setSelectedFile(file.filename); setFileContent(null); setFileMode("diff");
  }

  async function inspectPull(number: number, targetRepo = repo) {
    setSelectedPull(number); setPullLoading(true); setPullDetail(null); setPullTab("conversation");
    try { setPullDetail(await loadPull(targetRepo, number)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Failed to load pull request"); }
    finally { setPullLoading(false); }
  }

  function openPMWork(targetRepo: string, number: number) {
    setTab("pulls");
    if (targetRepo === repo) { void inspectPull(number); return; }
    setPendingPull({ repository: targetRepo, number });
    setRepo(targetRepo);
  }

  useEffect(() => {
    if (!pendingPull || pendingPull.repository !== repo) return;
    void inspectPull(pendingPull.number, pendingPull.repository);
    setPendingPull(null);
  }, [repo, pendingPull]);

  useEffect(() => {
    if (fileMode !== "file" || !selectedCommit || !selectedFile || !commitDetail) return;
    const currentFile = commitDetail.files.find((file) => file.filename === selectedFile);
    if (!currentFile) return;
    const sourceSha = currentFile.status === "removed" && commitDetail.parents[0] ? commitDetail.parents[0] : selectedCommit;
    const sourcePath = currentFile.status === "removed" ? currentFile.previousFilename || currentFile.filename : currentFile.filename;
    let cancelled = false;
    setFileLoading(true); setFileContent(null);
    loadFile(repo, sourceSha, sourcePath).then((content) => { if (!cancelled) setFileContent(content); }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Failed to load file"); }).finally(() => { if (!cancelled) setFileLoading(false); });
    return () => { cancelled = true; };
  }, [fileMode, repo, selectedCommit, selectedFile, commitDetail]);

  const openPullModels = useMemo(() => snapshot ? buildPullRequestGraph(
    snapshot.pulls.filter((pull) => (pull.lifecycle ?? "open") === "open"),
    { mainBranch: snapshot.defaultBranch, relations: snapshot.pullRelations },
  ) : [], [snapshot]);
  const flowByNumber = useMemo(() => new Map(openPullModels.map((pull) => [pull.number, pull])), [openPullModels]);
  const aiRanks = useMemo(() => new Map((aiProject?.prPriorities ?? []).filter((item) => item.repository === repo).map((item) => [item.number, item.rank])), [aiProject, repo]);
  const unreadActivityCount = useMemo(() => activity.filter((item) => item.id > lastSeenActivityId).length, [activity, lastSeenActivityId]);

  function markActivityRead() {
    const newest = activity[0]?.id ?? lastSeenActivityId;
    if (newest > lastSeenActivityId) setLastSeenActivityId(newest);
  }
  const pullCounts = useMemo(() => {
    const pulls = snapshot?.pulls ?? [];
    return {
      all: pulls.length,
      open: pulls.filter((pull) => (pull.lifecycle ?? "open") === "open").length,
      merged: pulls.filter((pull) => pull.lifecycle === "merged").length,
      closed: pulls.filter((pull) => pull.lifecycle === "closed").length,
    };
  }, [snapshot]);
  const visiblePulls = useMemo(() => (snapshot?.pulls ?? []).filter((pull) => {
    const stateMatch = pullFilter === "all" || (pull.lifecycle ?? "open") === pullFilter;
    const searchMatch = !query || `${pull.number} ${pull.title} ${pull.author} ${pull.head} ${pull.base}`.toLowerCase().includes(query.toLowerCase());
    return stateMatch && searchMatch;
  }), [snapshot, pullFilter, query]);
  const graphPulls = useMemo(() => {
    if (!snapshot) return [];
    if (pullGraphScope === "filtered") return visiblePulls;
    const openNumbers = new Set(snapshot.pulls.filter((pull) => (pull.lifecycle ?? "open") === "open").map((pull) => pull.number));
    const activeNumbers = new Set(openNumbers);
    for (const edge of snapshot.pullRelations) {
      if (openNumbers.has(edge.source) || openNumbers.has(edge.target)) {
        activeNumbers.add(edge.source); activeNumbers.add(edge.target);
      }
    }
    return snapshot.pulls.filter((pull) => activeNumbers.has(pull.number));
  }, [snapshot, pullGraphScope, visiblePulls]);

  const hasInspector = Boolean((selectedCommit && tab === "commits") || (selectedPull && tab === "pulls"));
  const workbenchColumns = [
    ...(aiPanelOpen ? [`${aiPanelWidth}px`, "5px"] : []),
    "minmax(420px, 1fr)",
    ...(hasInspector ? ["5px", `${inspectorWidth}px`] : []),
  ].join(" ");

  function startPanelResize(side: "ai" | "inspector", startX: number) {
    const startWidth = side === "ai" ? aiPanelWidth : inspectorWidth;
    const move = (event: PointerEvent) => {
      const delta = event.clientX - startX;
      if (side === "ai") setAIPanelWidth(Math.max(280, Math.min(620, startWidth + delta)));
      else setInspectorWidth(Math.max(360, Math.min(920, startWidth - delta)));
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); document.body.classList.remove("is-resizing"); };
    document.body.classList.add("is-resizing");
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }

  return <main className="app-shell" data-testid="dashboard-root">
    <header className="app-titlebar">
      <div className="app-mark"><GitBranch size={15}/><strong>Dev Flow</strong></div>
      <div className="repo-picker"><select aria-label="Repository" value={repo} onChange={(event) => setRepo(event.target.value)}>{repos.map((item) => <option key={item}>{item}</option>)}</select></div>
      <div className="title-actions"><button className={`ai-toggle ${aiPanelOpen ? "active" : ""}`} onClick={() => setAIPanelOpen((value) => !value)} title="AI Project Manager"><Sparkles size={14}/>{unreadActivityCount > 0 && <span className="toolbar-unread">{unreadActivityCount > 99 ? "99+" : unreadActivityCount}</span>}</button><button className="theme-toggle" onClick={() => setTheme((value) => value === "dark" ? "light" : "dark")} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>{theme === "dark" ? <Sun size={14}/> : <Moon size={14}/>}</button><span className={`live-sync ${liveConnected ? "connected" : ""}`} title={liveConnected ? "GitHub events update automatically" : "Live events reconnecting"}><i/></span><button className={loading ? "is-loading" : ""} onClick={() => void refresh(repo, true)} disabled={loading} title="Refresh"><RefreshCw size={14}/></button></div>
    </header>

    <div className="app-toolbar">
      <div className="tabs"><button className={tab === "commits" ? "active" : ""} onClick={() => setTab("commits")}><GitCommit size={13}/> Commit Graph</button><button className={tab === "pulls" ? "active" : ""} onClick={() => setTab("pulls")}><GitPullRequest size={13}/> Pull Requests <span>{pullCounts.all}</span></button></div>
      <label className="graph-search"><Search size={13}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === "commits" ? "Filter commits, branches, authors…" : "Filter pull requests…"}/></label>
    </div>

    {tab === "pulls" && <div className="pr-filterbar">
      <div className="pr-state-filters">{(["all", "open", "merged", "closed"] as const).map((state) => <button key={state} className={pullFilter === state ? "active" : ""} onClick={() => { setPullFilter(state); if (showPullGraph && state !== "all") setPullGraphScope("filtered"); }}>{state[0].toUpperCase() + state.slice(1)} <span>{pullCounts[state]}</span></button>)}</div>
      <div className="pr-view-controls">
        <label className="pr-sort-control"><span>Sort</span><select value={pullSort} onChange={(event) => setPullSort(event.target.value as PullSort)}><option value="ai">AI priority · keep chains</option><option value="flow">Flow groups</option><option value="updated">Recently updated</option><option value="number">PR number</option></select></label>
        {showPullGraph && <div className="pr-graph-scope"><button className={pullGraphScope === "active" ? "active" : ""} onClick={() => setPullGraphScope("active")}>Active + linked</button><button className={pullGraphScope === "filtered" ? "active" : ""} onClick={() => setPullGraphScope("filtered")}>Current filter</button></div>}
        <button className={`relations-toggle ${showPullGraph ? "active" : ""}`} onClick={() => setShowPullGraph((value) => !value)}><GitMerge size={12}/>{showPullGraph ? "Hide relations" : "Show relations"}</button>
      </div>
    </div>}

    {error && <div className="error-banner"><AlertTriangle size={15}/><span>{error}</span><button onClick={() => setError(null)}><X size={13}/></button></div>}

    <section className={`workbench ${hasInspector ? "with-inspector" : ""} ${aiPanelOpen ? "with-ai" : ""}`} style={{ gridTemplateColumns: workbenchColumns }}>
      {aiPanelOpen && <><AIProjectSidebar state={aiProject} loading={aiLoading} currentRepo={repo} activity={activity} unreadCount={unreadActivityCount} onRefresh={() => void refreshAI(true)} onSelectPull={openPMWork} onMarkActivityRead={markActivityRead} onRefreshActivity={() => void refreshActivity()}/><div className="panel-resizer vertical" onPointerDown={(event) => { event.preventDefault(); startPanelResize("ai", event.clientX); }}/></>}
      <div className="graph-pane">
        {loading && !snapshot ? <div className="center-message">Loading…</div> : null}
        {snapshot && tab === "commits" && <CommitGraph
          commits={snapshot.commits}
          headSha={snapshot.headSha}
          defaultBranch={snapshot.defaultBranch}
          selected={selectedCommit}
          onSelect={(commit) => void inspectCommit(commit)}
          query={query}
          columnWidths={commitColumnWidths}
          onColumnWidths={setCommitColumnWidths}
        />}
        {snapshot && tab === "pulls" && (showPullGraph
          ? <PullRequestGraph
              pulls={graphPulls}
              relations={snapshot.pullRelations}
              flowByNumber={flowByNumber}
              selected={selectedPull}
              onSelect={(number) => void inspectPull(number)}
              sort={pullSort}
              aiRanks={aiRanks}
            />
          : <PullRequestList
              pulls={visiblePulls}
              relations={snapshot.pullRelations}
              flowByNumber={flowByNumber}
              selected={selectedPull}
              onSelect={(number) => void inspectPull(number)}
              sort={pullSort}
              aiRanks={aiRanks}
              columnWidths={prColumnWidths}
              onColumnWidths={setPrColumnWidths}
            />
        )}
      </div>
      {hasInspector && <div
        className="panel-resizer vertical"
        onPointerDown={(event) => { event.preventDefault(); startPanelResize("inspector", event.clientX); }}
      />}
      {tab === "commits" && selectedCommit && <CommitInspector
        repo={repo}
        detail={commitDetail}
        loading={commitLoading}
        selectedFile={selectedFile}
        onFile={(file) => void selectFile(file)}
        fileContent={fileContent}
        fileLoading={fileLoading}
        mode={fileMode}
        setMode={setFileMode}
        ai={commitAI}
        aiLoading={commitAILoading}
        onPullSelect={(number) => { setTab("pulls"); void inspectPull(number); }}
        onClose={() => { setSelectedCommit(null); setCommitDetail(null); setCommitAI(null); }}
      />}
      {tab === "pulls" && selectedPull && <PullRequestInspector
        repo={repo}
        detail={pullDetail}
        loading={pullLoading}
        tab={pullTab}
        setTab={setPullTab}
        onClose={() => { setSelectedPull(null); setPullDetail(null); }}
      />}
    </section>
  </main>;
}
