// Canonical document edits only. Selection, measurements and Runtime snapshots stay separate.
export const cloneDocument = value => JSON.parse(JSON.stringify(value));

export function selectedFragment(graph, ids) {
  const selected = new Set(ids);
  const nodes = graph.nodes.filter(n => selected.has(n.id) && n.kind !== 'start');
  const included = new Set(nodes.map(n => n.id));
  return cloneDocument({nodes, edges: graph.edges.filter(e => included.has(e.source) && included.has(e.target))});
}

export function pasteFragment(graph, fragment, makeId, offset = 40) {
  const next = cloneDocument(graph);
  const mapping = new Map(fragment.nodes.map(n => [n.id, makeId('n')]));
  for (const original of fragment.nodes) {
    const node = cloneDocument(original);
    node.id = mapping.get(original.id);
    node.name += ' 副本';
    node.position = {x: (node.position?.x || 0) + offset, y: (node.position?.y || 0) + offset};
    // A document copy never silently creates a second writer for a native UUID.
    const freshSessions = n => {
      if (n.kind === 'agent') { n.session = 'new'; delete n.context_from; }
      if (n.kind === 'loop') n.body.nodes.forEach(freshSessions);
    };
    freshSessions(node);
    next.nodes.push(node);
  }
  for (const edge of fragment.edges) next.edges.push({...cloneDocument(edge), id: makeId('e'),
    source: mapping.get(edge.source), target: mapping.get(edge.target)});
  return {graph: next, selected: [...mapping.values()]};
}

export function removeSelection(graph, ids, edgeIds = []) {
  const next = cloneDocument(graph), nodes = new Set(ids), edges = new Set(edgeIds);
  next.nodes = next.nodes.filter(n => !nodes.has(n.id));
  next.edges = next.edges.filter(e => !nodes.has(e.source) && !nodes.has(e.target) && !edges.has(e.id));
  return next;
}

export function arrangeSelection(graph, ids, action, sizes = {}) {
  const next = cloneDocument(graph), selected = new Set(ids);
  const nodes = next.nodes.filter(n => selected.has(n.id));
  if (nodes.length < 2) return next;
  for (const n of nodes) n.position = {...(n.position || {x: 0, y: 0})};
  const width = n => sizes[n.id]?.width || 224, height = n => sizes[n.id]?.height || 140;
  const left = Math.min(...nodes.map(n => n.position.x)), top = Math.min(...nodes.map(n => n.position.y));
  const right = Math.max(...nodes.map(n => n.position.x + width(n)));
  const bottom = Math.max(...nodes.map(n => n.position.y + height(n)));
  if (action === 'horizontal' || action === 'vertical') {
    if (nodes.length < 3) return next;
    const horizontal = action === 'horizontal', key = horizontal ? 'x' : 'y';
    const size = horizontal ? width : height;
    nodes.sort((a, b) => a.position[key] - b.position[key]);
    const first = nodes[0].position[key], last = nodes.at(-1).position[key] + size(nodes.at(-1));
    const gap = (last - first - nodes.reduce((sum, n) => sum + size(n), 0)) / (nodes.length - 1);
    let position = first;
    for (const n of nodes) { n.position[key] = position; position += size(n) + gap; }
  } else for (const n of nodes) {
    if (action === 'left') n.position.x = left;
    if (action === 'right') n.position.x = right - width(n);
    if (action === 'top') n.position.y = top;
    if (action === 'bottom') n.position.y = bottom - height(n);
    if (action === 'center-x') n.position.x = (left + right - width(n)) / 2;
    if (action === 'center-y') n.position.y = (top + bottom - height(n)) / 2;
  }
  return next;
}

export function validConnection(graph, source, target, exceptEdge) {
  if (!source || !target || source === target) return false;
  const from = graph.nodes.find(n => n.id === source), to = graph.nodes.find(n => n.id === target);
  if (!from || !to || from.kind === 'result' || to.kind === 'start') return false;
  const edges = graph.edges.filter(e => e.id !== exceptEdge);
  if (edges.some(e => e.source === source && e.target === target)) return false;
  const pending = [target], seen = new Set();
  while (pending.length) {
    const id = pending.pop();
    if (id === source) return false;
    if (seen.has(id)) continue;
    seen.add(id); pending.push(...edges.filter(e => e.source === id).map(e => e.target));
  }
  return true;
}

// Preview every resulting connection before offering or applying an insertion.
// The temporary node never becomes part of a saved document or Runtime state.
export function canAddConnectedKind(graph, pending, kind) {
  if (!graph || !pending) return false;
  const original = pending.splitEdge ? graph.edges.find(e => e.id === pending.splitEdge) : null;
  if (pending.splitEdge && !original) return false;
  const source = original?.source || pending.source, target = original?.target || pending.target;
  let id = '__connection_preview__';
  while (graph.nodes.some(n => n.id === id)) id += '_';
  const preview = {nodes: [...graph.nodes, {id, kind}], edges: graph.edges.filter(e => e !== original)};
  if (source) {
    if (!validConnection(preview, source, id)) return false;
    preview.edges.push({id: '__connection_preview_edge__', source, target: id});
  }
  return !target || validConnection(preview, id, target);
}

export function inspectorPlacement(rect, bounds, width = 370) {
  const gap = 16, top = Math.max(bounds.top + gap, Math.min(rect.top, bounds.bottom - 400));
  if (rect.right + width + gap <= bounds.right) return {pinned: false, x: rect.right + gap, y: top};
  if (rect.left - width - gap >= bounds.left) return {pinned: false, x: rect.left - width - gap, y: top};
  return {pinned: true, x: bounds.right - width, y: bounds.top};
}


// Find genuinely empty screen space rather than covering a neighbouring node.
// Narrow editors may use the space below the graph; the camera never moves.
export function unobscuredInspectorPlacement(rect,bounds,occupied,viewportBottom,width=360){
 const gap=12,intersects=(a,b)=>a.x<b.right+gap&&a.x+width>b.left-gap&&a.y<b.bottom+gap&&a.y+a.height>b.top-gap;
 const visible=occupied.filter(r=>r.right>bounds.left&&r.left<bounds.right&&r.bottom>bounds.top&&r.top<bounds.bottom);
 const fit=c=>c.height>=180&&c.x>=bounds.left+gap&&c.x+width<=bounds.right-gap&&!visible.some(r=>intersects(c,r));
 const sideY=Math.max(bounds.top+gap,Math.min(rect.top,viewportBottom-300));
 const bottom=Math.max(bounds.top,...visible.map(r=>r.bottom))+gap;
 const top=Math.min(bounds.bottom,...visible.map(r=>r.top))-gap;
 const x=Math.max(bounds.left+gap,Math.min(rect.left,bounds.right-width-gap));
 const candidates=[{x:rect.right+gap,y:sideY,height:Math.min(420,viewportBottom-sideY)},
  {x:rect.left-width-gap,y:sideY,height:Math.min(420,viewportBottom-sideY)},
  {x,y:bottom,height:Math.min(420,viewportBottom-bottom)},
  {x,y:bounds.top+gap,height:Math.min(420,top-bounds.top-gap)}];
 const chosen=candidates.find(fit);return chosen?{pinned:false,...chosen}: {pinned:true};
}
