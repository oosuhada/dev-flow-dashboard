import { ROW_HEIGHT } from "./constants";
import type { GraphBranch, GraphLine, GraphStroke } from "./types";
import { laneX, rowY } from "./utils";

type PlacedLine = { x1: number; y1: number; x2: number; y2: number; lockedFirst: boolean };
function placeLine(line: GraphLine, rowHeight: number): PlacedLine {
  return { x1: laneX(line.p1.x), y1: rowY(line.p1.y, rowHeight), x2: laneX(line.p2.x), y2: rowY(line.p2.y, rowHeight), lockedFirst: line.lockedFirst };
}
function placeLines(branch: GraphBranch, rowHeight: number): PlacedLine[] {
  const lines = branch.lines.map((line) => placeLine(line, rowHeight));
  for (let i = 0; i < lines.length - 1;) {
    const line = lines[i], next = lines[i + 1];
    const straight = line.x1 === line.x2 && line.x2 === next.x1 && next.x1 === next.x2 && line.y2 === next.y1;
    if (straight) { line.y2 = next.y2; lines.splice(i + 1, 1); }
    else i++;
  }
  return lines;
}

export function branchStrokes(branch: GraphBranch, angular = false, rowHeight = ROW_HEIGHT): GraphStroke[] {
  const lines = placeLines(branch, rowHeight);
  const corner = rowHeight * (angular ? 0.38 : 0.8);
  let path = "";
  lines.forEach((line, i) => {
    const previous = lines[i - 1];
    if (path === "" || (previous !== undefined && (line.x1 !== previous.x2 || line.y1 !== previous.y2))) {
      path += `M${line.x1.toFixed(0)},${line.y1.toFixed(1)}`;
    }
    if (line.x1 === line.x2) {
      path += `L${line.x2.toFixed(0)},${line.y2.toFixed(1)}`;
    } else if (angular) {
      const corner1 = line.lockedFirst ? `${line.x2.toFixed(0)},${(line.y2 - corner).toFixed(1)}` : `${line.x1.toFixed(0)},${(line.y1 + corner).toFixed(1)}`;
      path += `L${corner1}L${line.x2.toFixed(0)},${line.y2.toFixed(1)}`;
    } else {
      path += `C${line.x1.toFixed(0)},${(line.y1 + corner).toFixed(1)} ${line.x2.toFixed(0)},${(line.y2 - corner).toFixed(1)} ${line.x2.toFixed(0)},${line.y2.toFixed(1)}`;
    }
  });
  return path ? [{ path, colour: branch.colour }] : [];
}
