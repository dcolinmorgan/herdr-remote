// ---- Triage: the one ordering the whole page agrees on ----------------------
//
// What needs you, then what is newly ready, then what is running, then everything else by when you
// last touched it. The herd list, the space chips and the tab chips all route through `bucketOf`, so
// a list row and a chip cannot come to disagree about what a colour means.
//
// It runs on the two timestamps the relay keeps per pane (see CLAUDE.md, "Pane activity"):
//   last_active_at -- when the agent last changed status
//   last_seen_at   -- when you last opened or drove it through the relay
const TRIAGE_ORDER = ['needs', 'ready', 'working', 'recent'];
// One colour per bucket rather than per status, which is the whole point: `done` means two
// different things depending on whether you have looked at it, and only the bucket knows which.
// Orange for ready sits where it belongs on red -> orange -> green -> grey, and leaves blue to mean
// selection, which is all it means anywhere else on this page.
const TRIAGE_COLOR = {
  needs: 'var(--red)', ready: 'var(--orange)', working: 'var(--green)', recent: 'var(--muted)',
};
const TRIAGE_META = {
  needs: {label: 'Needs you', accent: true},
  ready: {label: 'Ready · unseen'},
  working: {label: 'Working'},
  // The only foldable section, and the only one the direction toggle reaches. Collapsing an alert
  // defeats the alert, so the three above are pinned open.
  recent: {label: 'Recent', collapsible: true},
};

/** An agent that finished while you were not looking.
 *
 * NOT a stored flag -- it is this comparison, which is why opening the pane clears it with no
 * bookkeeping: the read bumps `last_seen_at` past `last_active_at` and the row falls into Recent on
 * the next snapshot. Both timestamps absent (a relay older than them) yields false, so the section
 * is simply empty there. */
function isUnseen(p) {
  return p.status === 'done' && (p.last_active_at || 0) > (p.last_seen_at || 0);
}

/** The single classifier. */
function bucketOf(p) {
  if (p.status === 'blocked') return 'needs';
  if (isUnseen(p)) return 'ready';
  if (p.status === 'working') return 'working';
  return 'recent';
}

/** The most urgent bucket in a set -- what a tab or space chip should advertise. Null when the set
 *  holds no agent at all, which is deliberately NOT the same as idle: an empty tab has nothing to
 *  report, and giving it a resting dot would claim otherwise. */
function worstTriage(list) {
  let best = null;
  for (const p of list) {
    const rank = TRIAGE_ORDER.indexOf(bucketOf(p));
    if (best === null || rank < best) best = rank;
  }
  return best === null ? null : TRIAGE_ORDER[best];
}

/** Bucket and order the herd. Empty sections come back too; the caller drops them, which keeps
 *  "which sections exist" a property of this function rather than of each view.
 *
 *  The no-timestamp path is free: every comparator returns 0, `Array.prototype.sort` is stable, so
 *  each section keeps the order the relay already sent. No feature detection, no branch. */
function triage(list, dir) {
  const into = {needs: [], ready: [], working: [], recent: []};
  for (const p of list) into[bucketOf(p)].push(p);
  const byDesc = key => (x, y) => (key(y) || 0) - (key(x) || 0);
  const active = byDesc(p => p.last_active_at);
  into.needs.sort(active);
  into.ready.sort(active);
  into.working.sort(active);
  into.recent.sort(byDesc(p => p.last_seen_at));
  if (dir === 'oldest') into.recent.reverse();
  return TRIAGE_ORDER.map(key => ({key, ...TRIAGE_META[key], panes: into[key]}));
}

// Recent's two controls, remembered because a phone reopens this page constantly and a fold that
// forgets itself is a fold nobody uses.
let recentDir = localStorage.getItem('herdr_recent_dir') === 'oldest' ? 'oldest' : 'newest';
let recentOpen = localStorage.getItem('herdr_recent_open') !== '0';
function flipRecentDir() {
  recentDir = recentDir === 'newest' ? 'oldest' : 'newest';
  localStorage.setItem('herdr_recent_dir', recentDir);
  render();
}
function toggleRecentOpen() {
  recentOpen = !recentOpen;
  localStorage.setItem('herdr_recent_open', recentOpen ? '1' : '0');
  render();
}

