import type { Vertex } from "./types";

export function createVertex(y: number): Vertex {
  return {
    y,
    parents: [],
    nextParent: 0,
    branch: null,
    x: 0,
    nextX: 0,
    connections: [],
    isCurrent: false,
  };
}
