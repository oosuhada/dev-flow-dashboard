import type { CommitRecord } from "../api";
import { createBranchColours } from "./branchColours";
import type { Branch, GraphBranch, GraphLayout, GraphLine, GraphVertex, Vertex } from "./types";
import { connectionTo, joinBranch, nextPointOf, pointOf, takePoint } from "./utils";
import { createVertex } from "./vertex";

type BranchColours = ReturnType<typeof createBranchColours>;
function nextParentOf(vertex: Vertex): Vertex | null { return vertex.parents[vertex.nextParent] ?? null; }
function addLine(branch: Branch, line: GraphLine) { branch.lines.push(line); }

function buildVertices(commits: CommitRecord[], commitHead: string | null): Vertex[] {
  const vertices = commits.map((_, index) => createVertex(index));
  const lookup = new Map(commits.map((commit, index) => [commit.sha, index]));
  commits.forEach((commit, index) => {
    for (const parentHash of commit.parents) {
      const parent = lookup.get(parentHash);
      if (parent !== undefined) vertices[index].parents.push(vertices[parent]);
    }
  });
  const head = commitHead === null ? undefined : lookup.get(commitHead);
  if (head !== undefined) vertices[head].isCurrent = true;
  return vertices;
}

function findStart(vertices: Vertex[]): number {
  return vertices.findIndex((vertex) => nextParentOf(vertex) !== null || vertex.branch === null);
}

function traceMerge(vertices: Vertex[], startAt: number) {
  const vertex = vertices[startAt];
  const parentVertex = nextParentOf(vertex)!;
  const parentBranch = parentVertex.branch!;
  let lastPoint = pointOf(vertex);
  for (let i = startAt + 1; i < vertices.length; i++) {
    const connection = connectionTo(vertices[i], parentVertex, parentBranch);
    const curPoint = connection ?? nextPointOf(vertices[i]);
    addLine(parentBranch, {
      p1: lastPoint,
      p2: curPoint,
      lockedFirst: connection === null && vertices[i] !== parentVertex ? lastPoint.x < curPoint.x : true,
    });
    takePoint(vertices[i], curPoint.x, parentVertex, parentBranch);
    lastPoint = curPoint;
    if (connection !== null) break;
  }
  vertex.nextParent++;
}

function traceBranch(vertices: Vertex[], startAt: number, colours: BranchColours): Branch {
  let vertex = vertices[startAt];
  let parentVertex = nextParentOf(vertex);
  const branch: Branch = { colour: colours.claim(startAt), lines: [] };
  let lastPoint = vertex.branch === null ? nextPointOf(vertex) : pointOf(vertex);
  joinBranch(vertex, branch, lastPoint.x);
  takePoint(vertex, lastPoint.x, vertex, branch);

  let i = startAt + 1;
  for (; i < vertices.length; i++) {
    const onParent = parentVertex === vertices[i];
    const curPoint = onParent && parentVertex!.branch !== null ? pointOf(vertices[i]) : nextPointOf(vertices[i]);
    addLine(branch, { p1: lastPoint, p2: curPoint, lockedFirst: lastPoint.x < curPoint.x });
    takePoint(vertices[i], curPoint.x, parentVertex, branch);
    lastPoint = curPoint;
    if (onParent) {
      vertex.nextParent++;
      const parentWasOnBranch = parentVertex!.branch !== null;
      joinBranch(parentVertex!, branch, curPoint.x);
      vertex = parentVertex!;
      parentVertex = nextParentOf(vertex);
      if (parentWasOnBranch) break;
    }
  }
  colours.release(branch.colour, i);
  return branch;
}

function toGraphBranch(branch: Branch): GraphBranch { return { colour: branch.colour, lines: branch.lines }; }
function toGraphVertex(vertex: Vertex): GraphVertex {
  return { x: vertex.x, y: vertex.y, colour: vertex.branch?.colour ?? 0, isCurrent: vertex.isCurrent };
}

export function computeGraphLayout(commits: CommitRecord[], commitHead: string | null): GraphLayout {
  const vertices = buildVertices(commits, commitHead);
  const colours = createBranchColours();
  const branches: Branch[] = [];
  let startAt = findStart(vertices);
  let iterations = 0;
  const iterationLimit = Math.max(32, vertices.length * vertices.length * 2);
  while (startAt !== -1 && iterations++ < iterationLimit) {
    const vertex = vertices[startAt];
    const parentVertex = nextParentOf(vertex);
    const isMergeOfTwoBranches = parentVertex !== null && vertex.parents.length > 1 && vertex.branch !== null && parentVertex.branch !== null;
    if (isMergeOfTwoBranches) traceMerge(vertices, startAt);
    else branches.push(traceBranch(vertices, startAt, colours));
    startAt = findStart(vertices);
  }
  return {
    branches: branches.map(toGraphBranch),
    vertices: vertices.map(toGraphVertex),
    lanes: vertices.reduce((lanes, vertex) => Math.max(lanes, vertex.nextX), 0),
  };
}
