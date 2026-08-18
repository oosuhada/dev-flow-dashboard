# Dev Flow Dashboard

GitHub PR/commit 흐름과 Gemini 기반 AI Project Manager를 한 화면에서 운영하기 위한 always-on developer console입니다.

**Live:** https://ontology.oosu.dev/dev_dashboard

## Why this exists

팀 프로젝트를 진행하면서 계약·명세 문서는 계속 정교해졌지만, 이미 합의된 내용을 다시 문서화하는 데 시간이 쓰이고 실제 구현·통합·검증이 늦어지는 문제가 있었습니다. 그래서 **합의된 문서를 기준선으로 두고 먼저 코딩과 통합을 진행한 뒤, 구현 과정에서 필요한 문서만 보정하는 execution flow**로 바꾸기 위해 이 대시보드를 만들었습니다.

PR 수가 늘어나면 GitHub의 개별 PR 화면만으로는 다음 질문에 답하기 어려워집니다.

- 무엇부터 리뷰해야 하는가?
- 다음 행동의 owner는 누구인가?
- 어떤 PR이 downstream 작업을 막고 있는가?
- 어떤 작업은 계속 진행하고, 어떤 작업은 폐기해야 하는가?
- 팀이 문서 정리나 확장 작업에 머물지 않고 실제 E2E 완료 방향으로 가고 있는가?

Dev Flow Dashboard는 **Git Graph 스타일의 deterministic 개발 흐름**을 source of truth로 두고, 그 위에 **Vertex AI Gemini 3.7 Flash 기반 AI Project Manager**를 결합합니다. AI는 GitHub 사실을 바꾸지 않고, 현재 팀이 무엇을 해야 하는지 해석하는 coordination layer로만 동작합니다.

## Screenshots

### Pull Request Flow

![Pull Request Flow](docs/screenshots/pull-request-flow.png)

### AI Project Manager

![AI Project Manager](docs/screenshots/ai-project-manager.png)

### AI PM Chat

![AI PM Chat](docs/screenshots/ai-pm-chat.png)

### Activity Inbox

![Activity Inbox](docs/screenshots/activity-inbox.png)

### Commit Graph

![Commit Graph](docs/screenshots/commit-graph.png)

## Pull Request Flow

Pull Requests가 기본 진입 화면이며, 새 브라우저에서는 **Open + relations ON**으로 시작합니다.

- Open / Merged / Closed를 포함한 전체 PR lifecycle 조회
- branch 관계를 이용한 PR dependency/relations graph
- AI priority / Flow groups / recently updated 정렬
- Pull request / Author / Branch / Relations / Review·State / Updated의 6개 공유 컬럼
- List와 Relations Graph가 동일한 column width state와 resize handle 사용
- 컬럼 크기와 panel 크기 localStorage 유지
- human review / automated review / CI 상태를 별도 신호로 표시
- PR inspector에서 body, review, comment, commit, check 확인
- Markdown + collapsible review/comment cards

Relations Graph는 별도의 고정 폭 테이블을 만드는 대신 첫 번째 PR 컬럼 안에 graph lane을 overlay합니다. 따라서 List ↔ Graph 전환 중에도 동일한 여섯 컬럼의 정렬과 사용자가 조절한 폭이 유지됩니다.

## Commit Graph

Commit Graph는 현재 브랜치·태그·commit topology와 commit metadata를 함께 보여줍니다.

- Git Graph 스타일 branch/lane rendering
- branch / tag / HEAD markers
- commit inspector와 changed-file tree
- diff / current file preview
- 관련 PR과 AI impact analysis
- commit columns resize + persistence

Density level이 올라갈 때 commit row height와 SVG graph 좌표도 함께 증가하므로 글자만 커지고 선/노드가 어긋나는 문제가 없습니다.

## AI Project Manager

AI PM은 프로젝트의 canonical docs와 현재 GitHub 상태를 함께 읽습니다.

- **Gemini 3.7 Flash on Vertex AI**
- canonical project docs에서 goal, role ownership, execution steps, out-of-scope, anti-overengineering rule을 추출한 Project Charter Memory
- 직전 PM context와 최근 webhook trigger를 다음 판단에 전달하는 rolling context
- 네 명의 팀원별 현재 `NOW` action
- PR priority queue, bottleneck, blocker, handoff, stop-doing signal
- 현재 execution step과 exit gate 판단
- PM judgement를 SQLite snapshot으로 저장하고 과거 시점 판단 복원

### Deterministic human review guard

LLM 응답과 무관하게 사람의 `CHANGES_REQUESTED`는 hard merge blocker로 처리합니다.

1. 해당 PR author가 요청사항을 반영하고 수정 commit을 push
2. 요청한 human reviewer에게 재검증 요청
3. reviewer의 최신 decisive state가 `APPROVED` 또는 `DISMISSED`가 되기 전에는 merge 권고 금지
4. green CI나 automated review는 이 guard를 해제하지 않음
5. 다만 superseded PR처럼 정답이 merge가 아니라 Close/폐기라면 바로 폐기 가능

이 규칙은 후속 unrelated webhook이 들어와도 현재 snapshot에 unresolved human request가 남아 있는 한 다시 적용됩니다.

## AI PM Chat

Chat은 별도의 blank assistant가 아니라 같은 Project Charter Memory와 현재 PM/GitHub context를 사용합니다. 예를 들어 다음 질문을 바로 할 수 있습니다.

- 지금 가장 큰 병목은 뭐야?
- 각 팀원이 지금 해야 할 일 알려줘
- 지금 머지해야 할 PR 순서 알려줘
- 오버엔지니어링 중인 작업이 있어?
- 다음 handoff는 누구에게 해야 해?

## Activity Inbox

GitHub webhook과 AI PM 판단을 SQLite Activity store에 기록하고 SSE로 열린 브라우저에 전달합니다.

- AI PM / PR / Review / Comment / Push / CI category filter
- repository filter
- 비슷한 이벤트 group / expand
- AI PM event 클릭 → 해당 historical PM judgement
- PR/review/comment 클릭 → PR inspector
- Push/CI 클릭 → commit inspector
- unread state localStorage 유지

## Live updates and fallback

브라우저는 GitHub API를 직접 호출하지 않습니다. FastAPI backend가 GitHub aggregator 역할을 하며 자격 증명은 서버에만 존재합니다.

- GitHub webhook → server-side snapshot refresh
- webhook/AI/activity event → SSE live update
- noisy CI event는 짧게 coalesce해 stale AI queue 방지
- 마지막 성공 GitHub snapshot을 `.state/snapshots/`에 저장
- GitHub rate limit 또는 일시 장애 시 stale snapshot으로 workbench 유지
- AI PM judgement와 Activity는 SQLite에 보존

## Density, theme, and resizing

브라우저 zoom 대신 5단계 density/font control을 제공합니다. 상단의 `Aa1`~`Aa5` 버튼은 `1 → 2 → 3 → 4 → 5 → 1`로 순환하며 localStorage에 저장됩니다.

Density는 font-size만 바꾸지 않습니다. 단계가 올라갈수록 다음 요소가 함께 조금씩 커집니다.

- text / line-height
- PR·commit row height
- control / button / chip height
- padding / gap
- AI PM card spacing
- Activity row spacing
- Chat message/input spacing

반대로 PR/Commit column width, AI sidebar width, inspector width, page 전체 geometry는 zoom처럼 비례 확대하지 않습니다. 1280px 폭에서도 sidebar + inspector가 viewport 밖으로 밀리지 않도록 panel track에는 responsive upper bound가 적용됩니다.

Light/Dark theme, AI sidebar, right inspector, PR/commit columns는 각각 독립적으로 동작합니다.

## Architecture

```text
GitHub repositories
  ├─ REST API ───────────────┐
  └─ Webhooks ───────────────┤
                             v
                      FastAPI aggregator
                      ├─ deterministic graph/review state
                      ├─ stale snapshot fallback
                      ├─ SQLite activity + PM history
                      ├─ Project Charter Memory
                      └─ Vertex AI Gemini 3.7 Flash
                             │
                             ├─ SSE live events
                             └─ /api/*
                                  │
                                  v
                           React + Vite frontend
```

The deterministic Git/GitHub layer is authoritative for topology, lifecycle, reviews, checks, merge state, authorship, and dependency facts. Gemini is an advisory interpretation layer and does not mutate GitHub.

## Production deployment

현재 live instance는 개인 Mac mini 홈서버에서 실행됩니다.

- app bind: `127.0.0.1:4310`
- macOS launchd: `com.oosu.dev-flow-dashboard`
- public ingress: Cloudflare Tunnel
- public path: `https://ontology.oosu.dev/dev_dashboard`
- launch script는 macOS의 낮은 기본 file-descriptor limit 때문에 SSE/webhook 연결이 고갈되지 않도록 soft limit를 올린 뒤 Uvicorn을 실행

서비스는 loopback에만 bind되고 public ingress는 tunnel이 담당합니다.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt

cd frontend
npm install
npm run build
cd ..

GITHUB_REPOSITORIES=owner/repo-a,owner/repo-b \
  .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 4310
```

For frontend-only work:

```bash
cd frontend
npm run dev
```

## Tests

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests

cd frontend
npm run lint
npm test
npm run build
```

Public browser validation is performed with Playwright using `domcontentloaded`; SSE is intentionally long-lived, so `networkidle` is not an appropriate readiness condition.

## Docker

```bash
cp .env.example .env
docker compose up -d --build
```

The containerized service also publishes only on `127.0.0.1:4310` by default so a reverse proxy or tunnel can own public ingress.

## Security

- GitHub/Vertex/webhook/tunnel credentials remain server-side.
- `.env`, `.state`, SQLite runtime DBs, generated credentials, and local virtual environments are not source artifacts.
- The browser only calls this service's API; it does not receive a GitHub token.
- Repository visibility should only be changed after checking both the current tracked tree and Git history for credentials.
- AI PM is read-only with respect to GitHub: it can recommend actions, not perform merges, closes, reviews, or comments.

## Attribution and license

The commit graph rendering code under `frontend/src/gitgraph/` preserves its MIT lineage and license notice in [`frontend/src/gitgraph/LICENSE`](frontend/src/gitgraph/LICENSE):

- **mhutchie Git Graph**
- **asispts/neo-git-graph** fork lineage

That MIT notice applies to the attributed Git Graph lineage described in that file. This repository currently does not add a separate top-level license grant for the rest of the project source.

