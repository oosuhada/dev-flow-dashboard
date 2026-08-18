import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ChevronRight,
  Code2,
  ExternalLink,
  FileCode2,
  File,
  Files,
  Folder,
  FolderOpen,
  GitBranch,
  GitCommit,
  GitPullRequest,
  RefreshCw,
  Search,
  Tag,
  X,
} from "lucide-react";
import {
  loadCommit,
  loadFile,
  loadRepositories,
  loadSnapshot,
  type CommitDetail,
  type CommitFile,
  type CommitRecord,
  type FileContent,
  type Snapshot,
} from "./api";
import { buildPullRequestGraph, rankBottlenecks, type PullRequestModel } from "./graphModel";
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

function laneColor(lane: number) {
  return GRAPH_COLORS[lane % GRAPH_COLORS.length];
}

function CommitGraph({ commits, headSha, defaultBranch, selected, onSelect, query }: {
  commits: CommitRecord[];
  headSha: string | null;
  defaultBranch: string;
  selected: string | null;
  onSelect: (commit: CommitRecord) => void;
  query: string;
}) {
  const visible = useMemo(() => commits.slice(0, 100), [commits]);
  const head = headSha ?? visible[0]?.sha ?? null;
  const layout = useMemo(() => computeGraphLayout(visible, head), [visible, head]);
  const strokes = useMemo(() => layout.branches.flatMap((branch) => branchStrokes(branch, false)), [layout]);
  const width = Math.max(64, graphWidth(layout) + GRAPH_PADDING);
  const height = Math.max(ROW_HEIGHT, graphHeight(layout));
  const needle = query.trim().toLowerCase();

  return <div className="commit-table" data-testid="commit-graph">
    <div className="commit-table-head" style={{ gridTemplateColumns: `${width}px minmax(330px,1fr) 150px 130px 92px` }}>
      <span>Graph</span><span>Description</span><span>Date</span><span>Author</span><span>Commit</span>
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
      <div className="commit-rows" style={{ marginLeft: width }}>
        {visible.map((commit, index) => {
          const colour = layout.vertices[index]?.colour ?? 0;
          const orderedRefs = [...commit.refs].sort((a, b) => Number(b.type === "head" && b.name === defaultBranch) - Number(a.type === "head" && a.name === defaultBranch));
          const haystack = `${commit.sha} ${commit.message} ${commit.author} ${commit.refs.map((ref) => ref.name).join(" ")} ${commit.prNumber ?? ""}`.toLowerCase();
          const filteredOut = needle !== "" && !haystack.includes(needle);
          return <button key={commit.sha} className={`commit-row ${selected === commit.sha ? "selected" : ""} ${filteredOut ? "filtered-out" : ""}`} onClick={() => onSelect(commit)}>
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

function CommitInspector({ repo, detail, loading, selectedFile, onFile, fileContent, fileLoading, mode, setMode, onClose }: {
  repo: string;
  detail: CommitDetail | null;
  loading: boolean;
  selectedFile: string | null;
  onFile: (file: CommitFile) => void;
  fileContent: FileContent | null;
  fileLoading: boolean;
  mode: "diff" | "file";
  setMode: (mode: "diff" | "file") => void;
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

function PullRequestView({ pulls, selected, onSelect }: { pulls: PullRequestModel[]; selected: number | null; onSelect: (value: number) => void }) {
  const ordered = [...pulls].sort((a, b) => b.bottleneckScore - a.bottleneckScore || b.number - a.number);
  return <div className="pr-table">
    <div className="pr-head"><span>Pull request</span><span>Branch</span><span>Review / CI</span><span>Impact</span></div>
    {ordered.map((pull) => <button key={pull.number} className={selected === pull.number ? "selected" : ""} onClick={() => onSelect(pull.number)}>
      <div><strong>#{pull.number}</strong><span>{pull.title}</span></div>
      <span className="pr-branch">{pull.head}<ChevronRight size={11}/>{pull.base}</span>
      <span className={`pr-status s-${statusClass(pull.status)}`}>{statusLabel(pull.status)}</span>
      <span>{pull.downstreamCount ? `blocks ${pull.downstreamCount}` : "—"}</span>
    </button>)}
  </div>;
}

export function App() {
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
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileMode, setFileMode] = useState<"diff" | "file">("diff");
  const [selectedPull, setSelectedPull] = useState<number | null>(null);

  useEffect(() => { loadRepositories().then((items) => { setRepos(items); setRepo((current) => current || items[0] || ""); }).catch((reason) => { setError(reason.message); setLoading(false); }); }, []);
  async function refresh(target: string, force = false) {
    if (!target) return;
    setLoading(true); setError(null);
    try { setSnapshot(await loadSnapshot(target, force)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Failed to load GitHub data"); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (repo) { setSelectedCommit(null); setCommitDetail(null); setSelectedFile(null); void refresh(repo); } }, [repo]);

  async function inspectCommit(commit: CommitRecord) {
    setSelectedCommit(commit.sha); setCommitLoading(true); setCommitDetail(null); setSelectedFile(null); setFileContent(null); setFileMode("diff");
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

  const pulls = useMemo(() => snapshot ? buildPullRequestGraph(snapshot.pulls) : [], [snapshot]);
  const bottlenecks = useMemo(() => rankBottlenecks(pulls).slice(0, 4), [pulls]);

  return <main className="app-shell" data-testid="dashboard-root">
    <header className="app-titlebar">
      <div className="app-mark"><GitBranch size={15}/><strong>Dev Flow</strong></div>
      <div className="repo-picker"><select aria-label="Repository" value={repo} onChange={(event) => setRepo(event.target.value)}>{repos.map((item) => <option key={item}>{item}</option>)}</select></div>
      <div className="title-actions"><span className="live-indicator"><i/>live</span><span>{snapshot ? `${snapshot.commits.length} commits · ${snapshot.pulls.length} open PRs` : "loading"}</span><button onClick={() => void refresh(repo, true)} disabled={loading} title="Refresh"><RefreshCw size={14}/></button></div>
    </header>

    <div className="app-toolbar">
      <div className="tabs"><button className={tab === "commits" ? "active" : ""} onClick={() => setTab("commits")}><GitCommit size={13}/> Commit Graph</button><button className={tab === "pulls" ? "active" : ""} onClick={() => setTab("pulls")}><GitPullRequest size={13}/> Pull Requests <span>{pulls.length}</span></button></div>
      <label className="graph-search"><Search size={13}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === "commits" ? "Filter commits, branches, authors…" : "Filter pull requests…"}/></label>
    </div>

    {error && <div className="error-banner"><AlertTriangle size={15}/><span>{error}</span><button onClick={() => setError(null)}><X size={13}/></button></div>}

    <section className={`workbench ${selectedCommit && tab === "commits" ? "with-inspector" : ""}`}>
      <div className="graph-pane">
        {loading && !snapshot ? <div className="center-message">Reading GitHub history…</div> : null}
        {snapshot && tab === "commits" && <CommitGraph
          commits={snapshot.commits}
          headSha={snapshot.headSha}
          defaultBranch={snapshot.defaultBranch}
          selected={selectedCommit}
          onSelect={(commit) => void inspectCommit(commit)}
          query={query}
        />}
        {snapshot && tab === "pulls" && <PullRequestView
          pulls={pulls.filter((pull) => !query || `${pull.number} ${pull.title} ${pull.author} ${pull.head}`.toLowerCase().includes(query.toLowerCase()))}
          selected={selectedPull}
          onSelect={setSelectedPull}
        />}
      </div>
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
        onClose={() => { setSelectedCommit(null); setCommitDetail(null); }}
      />}
    </section>

    <footer className="statusbar">
      <span><GitBranch size={11}/>{repo}</span>
      <span>{snapshot ? `${snapshot.authentication} GitHub · cache ${snapshot.cache.hit ? "hit" : "miss"} · ${snapshot.rateLimit.remaining ?? "?"}/${snapshot.rateLimit.limit ?? "?"}` : "connecting"}</span>
      {bottlenecks[0] && <button onClick={() => { setTab("pulls"); setSelectedPull(bottlenecks[0].number); }}>bottleneck: #{bottlenecks[0].number} · blocks {bottlenecks[0].downstreamCount}</button>}
    </footer>
  </main>;
}
