import {pasteFragment, selectedFragment} from '../../studio/workbench_ui/canvas_editing.mjs';

export const canvasFragment = (graph, ids) => selectedFragment(graph, ids, {includeStart: true});
export const coordinate = number => Math.min(100000, Math.max(-100000, number));

export function pasteCanvasFragment(graph, fragment, makeId) {
 if (graph.nodes.length + fragment.nodes.length > 64 || graph.edges.length + fragment.edges.length > 128)
  throw Error('粘贴后将超过 64 个节点或 128 条连线。');
 return pasteFragment(graph, fragment, makeId, 40, node => {
  // Frozen Agent references may point at one managed native session. Rebind the
  // copy explicitly instead of silently granting a second resume entry.
  delete node.agent_id;
  delete node.agent_revision;
  node.name = [...node.name].slice(0, 160).join('');
  node.position = {x: coordinate(node.position.x), y: coordinate(node.position.y)};
 });
}

export function applySelection(current, changes) {
 const next = new Set(current);
 for (const change of changes) {
  if (change.selected) next.add(change.id);
  else next.delete(change.id);
 }
 return [...next];
}
