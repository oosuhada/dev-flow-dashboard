import { LANE_OFFSET, LANE_WIDTH, ROW_HEIGHT } from "./constants";
import type { Branch, GraphLayout, GraphPoint, Vertex } from "./types";

export function laneX(x: number): number { return x * LANE_WIDTH + LANE_OFFSET; }
export function rowY(y: number): number { return y * ROW_HEIGHT + ROW_HEIGHT / 2; }
export function graphWidth(layout: GraphLayout): number { return layout.lanes * LANE_WIDTH; }
export function graphHeight(layout: GraphLayout): number { return layout.vertices.length * ROW_HEIGHT; }
export function pointOf(vertex: Vertex): GraphPoint { return { x: vertex.x, y: vertex.y }; }
export function nextPointOf(vertex: Vertex): GraphPoint { return { x: vertex.nextX, y: vertex.y }; }
export function connectionTo(vertex: Vertex, connectsTo: Vertex | null, onBranch: Branch): GraphPoint | null {
  const x = vertex.connections.findIndex((connection) => connection.connectsTo === connectsTo && connection.onBranch === onBranch);
  return x === -1 ? null : { x, y: vertex.y };
}
export function takePoint(vertex: Vertex, x: number, connectsTo: Vertex | null, onBranch: Branch): void {
  if (x === vertex.nextX) {
    vertex.nextX = x + 1;
    vertex.connections[x] = { connectsTo, onBranch };
  }
}
export function joinBranch(vertex: Vertex, branch: Branch, x: number): void {
  if (vertex.branch === null) {
    vertex.branch = branch;
    vertex.x = x;
  }
}
