// Adapted from asispts/neo-git-graph (MIT). See ./LICENSE.
import type { CommitFile } from "../api";

export type FileTreeFile = { type: "file"; name: string; file: CommitFile };
export type FileTreeFolder = { type: "folder"; name: string; path: string; children: FileTreeNode[] };
export type FileTreeNode = FileTreeFile | FileTreeFolder;

function sortNodes(nodes: FileTreeNode[]): FileTreeNode[] {
  for (const node of nodes) if (node.type === "folder") node.children = sortNodes(node.children);
  return [...nodes].sort((a, b) => a.type === b.type ? a.name.localeCompare(b.name) : a.type === "folder" ? -1 : 1);
}

export function buildFileTree(files: CommitFile[]): FileTreeNode[] {
  const root: FileTreeFolder = { type: "folder", name: "", path: "", children: [] };
  const folders = new Map<string, FileTreeFolder>([["", root]]);
  for (const file of files) {
    const parts = file.filename.split("/");
    let folder = root;
    for (const name of parts.slice(0, -1)) {
      const path = folder.path === "" ? name : `${folder.path}/${name}`;
      let child = folders.get(path);
      if (!child) {
        child = { type: "folder", name, path, children: [] };
        folders.set(path, child);
        folder.children.push(child);
      }
      folder = child;
    }
    folder.children.push({ type: "file", name: parts.at(-1) || file.filename, file });
  }
  return sortNodes(root.children);
}
