// Adapted from asispts/neo-git-graph (MIT), itself based on the last MIT
// release of mhutchie/vscode-git-graph. See ./LICENSE.
export type GraphPoint = { x: number; y: number };
export type GraphLine = { p1: GraphPoint; p2: GraphPoint; lockedFirst: boolean };
export type GraphBranch = { colour: number; lines: GraphLine[] };
export type GraphVertex = { x: number; y: number; colour: number; isCurrent: boolean };
export type GraphLayout = { branches: GraphBranch[]; vertices: GraphVertex[]; lanes: number };
export type GraphStroke = { path: string; colour: number };
export type Branch = { colour: number; lines: GraphLine[] };
export type Connection = { connectsTo: Vertex | null; onBranch: Branch };
export type Vertex = {
  readonly y: number;
  readonly parents: Vertex[];
  nextParent: number;
  branch: Branch | null;
  x: number;
  nextX: number;
  connections: Connection[];
  isCurrent: boolean;
};
