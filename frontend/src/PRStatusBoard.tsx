import {
  CheckCircle2,
  CircleDot,
  Clock3,
  ExternalLink,
  GitMerge,
  PauseCircle,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import board from "./pr-status-board.json";

type StatusKind = "ready" | "review" | "blocked" | "waiting" | "hold" | "verified";

type BoardItem = {
  number: number;
  area: string;
  status: StatusKind;
  decision: string;
  summary: string;
  action: string;
};

const items = board.items as BoardItem[];

const statusMeta: Record<StatusKind, { label: string; icon: typeof CheckCircle2 }> = {
  ready: { label: "Ready", icon: CheckCircle2 },
  review: { label: "Re-review", icon: CircleDot },
  blocked: { label: "Blocked", icon: XCircle },
  waiting: { label: "Waiting", icon: Clock3 },
  hold: { label: "Hold", icon: PauseCircle },
  verified: { label: "Verified", icon: ShieldCheck },
};

function updatedLabel(value: string) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(date);
}

export function PRStatusBoard({ query }: { query: string }) {
  const needle = query.trim().toLowerCase();
  const visible = items.filter((item) => {
    if (!needle) return true;
    return `#${item.number} ${item.area} ${item.decision} ${item.summary} ${item.action}`.toLowerCase().includes(needle);
  });
  const counts = items.reduce<Record<StatusKind, number>>((acc, item) => {
    acc[item.status] += 1;
    return acc;
  }, { ready: 0, review: 0, blocked: 0, waiting: 0, hold: 0, verified: 0 });
  const baseUrl = `https://github.com/${board.repository}/pull`;

  return <div className="status-board" data-testid="pr-status-board">
    <div className="status-board-hero">
      <div>
        <span className="status-board-kicker"><GitMerge size={12}/> Manual PR snapshot</span>
        <h1>{board.headline}</h1>
        <p>{board.note}</p>
      </div>
      <div className="status-board-meta">
        <b>{board.asOfLabel}</b>
        <span>{board.repository}</span>
        <strong>Updated {updatedLabel(board.updatedAt)}</strong>
        <small>{board.updatedBy}</small>
      </div>
    </div>

    <div className="status-board-counts">
      {(Object.keys(statusMeta) as StatusKind[]).map((status) => {
        const meta = statusMeta[status];
        const Icon = meta.icon;
        return <div className={`status-count status-${status}`} key={status}>
          <Icon size={14}/><span>{meta.label}</span><strong>{counts[status]}</strong>
        </div>;
      })}
    </div>

    <div className="status-board-plan">
      {board.mergePlan.map((step) => <div key={step.label}>
        <span>{step.label}</span>
        <strong>{step.value}</strong>
      </div>)}
    </div>

    <div className="status-board-table-wrap">
      <div className="status-board-table-head">
        <span>PR / Area</span><span>Current decision</span><span>Why</span><span>Next action</span>
      </div>
      <div className="status-board-rows">
        {visible.map((item) => {
          const meta = statusMeta[item.status];
          const Icon = meta.icon;
          return <article className="status-board-row" key={item.number}>
            <div className="status-pr-cell">
              <a href={`${baseUrl}/${item.number}`} target="_blank" rel="noreferrer">
                <strong>#{item.number}</strong><ExternalLink size={10}/>
              </a>
              <span>{item.area}</span>
            </div>
            <div>
              <span className={`status-badge status-${item.status}`}><Icon size={11}/>{item.decision}</span>
            </div>
            <p>{item.summary}</p>
            <p className="status-next-action">{item.action}</p>
          </article>;
        })}
        {visible.length === 0 && <div className="status-board-empty">No PR status matches this filter.</div>}
      </div>
    </div>
  </div>;
}
