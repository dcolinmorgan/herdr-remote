// ---- Naming lookups, built once per render ---------------------------------
//
// Per pane these were spaces x panes each time, and this page re-renders on every snapshot. One
// pass, then map lookups.
let spaceNameByKey = new Map();
let tabLabelByKey = new Map();
let tabCountBySpace = new Map();

function rebuildNameMaps(rows) {
  spaceNameByKey = new Map(rows.map(r => [r.key, r.name]));
  tabLabelByKey = new Map();
  tabCountBySpace = new Map();
  for (const t of spaces.tabs) {
    const wsKey = spaceKey(t.host, t.workspace_id);
    tabLabelByKey.set(spaceKey(t.host, t.tab_id), t.label || '');
    tabCountBySpace.set(wsKey, (tabCountBySpace.get(wsKey) || 0) + 1);
  }
}

/** A tab label worth putting on screen.
 *
 * herdr labels an unlabelled tab POSITIONALLY -- it returns "1", "2" -- so a naive join renders
 * `herdr-remote-dev · 1`, which reads as a bug rather than as a name. With one tab in the space
 * there is nothing to disambiguate, so the positional default is dropped. With two or more the
 * number stays: it is weak, but it is the only thing telling two panes in the same project apart,
 * and it is what the desk shows. */
function meaningfulTabLabel(label, tabCount) {
  const trimmed = (label || '').trim();
  if (!trimmed) return null;
  if (tabCount <= 1 && /^\d+$/.test(trimmed)) return null;
  return trimmed;
}

// A rebuild drops the selection the reader is holding. Both containers that carry selectable text
// are rewritten on a timer -- the mirror every 3s from `pane_content`, the herd list on every 2s
// `agents` snapshot -- and `replaceChildren`/`innerHTML` detach the very text nodes the range's
// endpoints live in, so the browser has nothing left to anchor it to and the highlight vanishes
// mid-copy. A selection could therefore not survive three seconds, which is less than it takes to
// reach the copy button on a phone.
//
// So a tick that would rewrite a container the reader is selecting inside does not run. Nothing is
// queued: the tick repeats, so what it skipped arrives on the next one once the selection is
// released. The pause explains itself -- the highlight is on screen -- and it is the only honest
// option, since there is no way to re-anchor a range to nodes that no longer exist.
function selectionInside(el) {
  if (!el) return false;
  const sel = window.getSelection && window.getSelection();
  if (!sel || !sel.rangeCount || sel.isCollapsed) return false;
  for (let i = 0; i < sel.rangeCount; i++) {
    const range = sel.getRangeAt(i);
    // The endpoints are TEXT nodes, and `Node.contains` takes any node -- comparing elements would
    // miss the common case, where both ends sit inside one text node.
    if (el.contains(range.startContainer) || el.contains(range.endContainer)) return true;
  }
  return false;
}

function render() {
  document.getElementById('agentCount').textContent = agents.length ? `${agents.length}` : '';
  const rows = workspaceRows();
  rebuildNameMaps(rows);
  // A terminal that appears or goes away beside the open pane changes the strips, and this is every
  // path a snapshot arrives by. Cheap: two arrays already in hand, at most eight chips. After the
  // name maps, not before, because the tabs row is named out of the same hierarchy they are.
  renderSiblings();
  // A space selection is only reachable while the strip that offers it is up, and the strip is up
  // exactly when the panes span more than one space.
  const occupied = new Set([...agents, ...shellPanes]
    .map(p => agentWorkspaceKey(p)).filter(k => !k.endsWith('|')));
  if (occupied.size <= 1) { activeWorkspace = null; activeTab = null; }
  const list = document.getElementById('agents');
  // Checked here rather than at the top of render(), so the name maps and the sibling strips still
  // track the truth while the list itself holds still.
  if (selectionInside(list)) return;
  const strips = chipStripScroll(list);
  list.innerHTML = activeWorkspace ? renderSpaceView(rows) : renderHerd(rows, occupied);
  restoreChipStripScroll(list, strips);
  bindLongPressHandlers();
}

// A rebuild is not a gesture, and this list rebuilds on every `agents` snapshot -- every two
// seconds, forever. The chip strips scroll SIDEWAYS (ten spaces do not fit across a phone), and
// `innerHTML` throws away the very elements that hold the offset, so a reader who had scrolled the
// Spaces row to reach the space at the far end watched it snap back to the beginning mid-reach.
// That is the reported bug, and it is the same one the sibling row has (see renderSiblings): the
// offset is the reader's, so it survives a rebuild it did not ask for.
//
// Keyed by WHAT the strip is (`data-strip`), not by its position in the list: the herd draws one
// strip and the space view draws two, and the Spaces row has to keep its place across that switch
// as well -- picking a space is exactly when you have just scrolled it.
function chipStripScroll(list) {
  const kept = new Map();
  for (const strip of list.querySelectorAll('.chip-strip')) {
    if (strip.scrollLeft) kept.set(strip.dataset.strip, strip.scrollLeft);
  }
  return kept;
}

function restoreChipStripScroll(list, kept) {
  if (!kept.size) return;
  for (const strip of list.querySelectorAll('.chip-strip')) {
    const x = kept.get(strip.dataset.strip);
    // Assigned, not clamped by hand: a strip that lost chips since the last snapshot is put at its
    // own new end by the browser rather than left pointing past it.
    if (x) strip.scrollLeft = x;
  }
}

// The herd, in the one order the app agrees on. AGENTS only: two thirds of the panes on a real host
// are bare shells with no status at all, and triaging them would bury ten agents under twenty rows
// that can never be anything but Recent. They are reached through the space view, which groups by
// tab, and through the sibling strip inside a session.
function renderHerd(rows, occupied) {
  let html = occupied.size > 1 ? spaceStrip(rows) : '';
  const sections = triage(agents, recentDir);
  for (const s of sections) {
    if (!s.panes.length) continue;
    html += sectionHeader(s);
    if (s.key === 'recent' && !recentOpen) continue;
    html += s.panes.map(p => agentCard(p, 'herd')).join('');
  }
  if (!agents.length) {
    html += shellPanes.length
      ? `<div class="empty">No agents running. ${shellPanes.length} terminal${shellPanes.length === 1 ? '' : 's'} — pick a space to reach them.</div>`
      // Nothing at all is the case that has a REASON worth printing -- mid-switch, or pointed at
      // a session that is not running -- so it goes through emptyStateHtml rather than repeating
      // its default here.
      : (spaces.workspaces.length ? '<div class="empty">No agents running.</div>'
                                  : emptyStateHtml());
  }
  return html;
}

function sectionHeader(s) {
  const tint = s.accent ? ` style="color:${TRIAGE_COLOR[s.key]}"` : '';
  let controls = '';
  if (s.collapsible) {
    // Direction reaches Recent and nothing else: an attention section is ordered by urgency, and
    // urgency does not invert.
    controls =
      `<button class="sec-btn" onclick="flipRecentDir()" aria-label="Sort ${recentDir === 'newest' ? 'oldest' : 'newest'} first">`
      + `${recentDir === 'newest' ? '↓' : '↑'}</button>`
      + `<button class="sec-btn" onclick="toggleRecentOpen()" aria-expanded="${recentOpen}"`
      + ` aria-label="${recentOpen ? 'Collapse' : 'Expand'} ${s.label}">${recentOpen ? '⌃' : '⌄'}</button>`;
  }
  return `<div class="section-header"${tint}><span class="dot" style="background:${TRIAGE_COLOR[s.key]}"></span>`
    + `<span class="sec-label">${escapeHtml(s.label)}</span> <span class="sec-count">(${s.panes.length})</span>${controls}</div>`;
}

// One space's panes, grouped by tab -- agents AND bare shells, which is the only view that shows
// both together and the only one where "what is in this tab" is the question being asked.
function renderSpaceView(rows) {
  let html = spaceStrip(rows) + tabStrip(rows);
  const groups = groupPanesByTab(activeWorkspace);
  const shown = activeTab ? groups.filter(g => g.key === activeTab) : groups;
  for (const g of shown) {
    if (!activeTab) html += tabHeading(g);
    html += g.panes.length
      ? g.panes.map(p => p.agent ? agentCard(p, 'tab') : shellCard(p, 'tab')).join('')
      // A freshly created tab holds one shell the relay may not have listed yet, and an empty tab is
      // a real thing to see rather than an absence to hide.
      : '<div class="empty-tab">(empty tab)</div>';
  }
  if (!shown.length) {
    html += `<div class="empty">${activeTab ? 'This tab has no panes.' : 'This space has no panes.'}</div>`;
  }
  return html;
}

function tabHeading(g) {
  const dot = worstTriage(g.panes.filter(p => p.agent));
  return `<div class="tab-heading">`
    + (dot ? `<span class="dot" style="background:${TRIAGE_COLOR[dot]}"></span>` : '')
    + `${escapeHtml(g.name)} <span class="sec-count">(${g.panes.length})</span></div>`;
}

/** A workspace's panes by tab, in tab order, including empty tabs. Panes whose tab is not in the
 *  tab list yet -- a brief poll race after a create -- fall into a trailing group so they are never
 *  lost -- the list is the panes, and the hierarchy only names and orders them. */
function groupPanesByTab(workspaceKey) {
  const panes = [], seen = new Set();
  for (const p of [...agents, ...shellPanes]) {
    // By pane_id: the two arrays are disjoint in a snapshot, but a `blocked` push can add an agent
    // record for a pane still sitting in shellPanes.
    if (seen.has(p.pane_id) || agentWorkspaceKey(p) !== workspaceKey) continue;
    seen.add(p.pane_id);
    panes.push(p);
  }
  const tabs = tabRows(workspaceKey);
  const groups = tabs.map(t => ({
    key: t.key, name: t.name, focused: t.focused,
    panes: panes.filter(p => agentTabKey(p) === t.key),
  }));
  const known = new Set(tabs.map(t => t.key));
  const orphans = panes.filter(p => !known.has(agentTabKey(p)));
  if (orphans.length) groups.push({key: `${workspaceKey}|other`, name: '…', panes: orphans});
  return groups;
}

function spaceStrip(rows) {
  let html = `<div class="chip-strip" data-strip="spaces"><span class="chip-label">Spaces</span>`;
  html += `<button class="chip${activeWorkspace === null ? ' active' : ''}" onclick="backToWorkspaces()">All</button>`;
  for (const w of rows) {
    const held = [...agents, ...shellPanes].filter(p => agentWorkspaceKey(p) === w.key);
    // One dot from the one classifier, so this chip and the row it stands for cannot disagree. No
    // dot at all when the space holds no agent -- see worstTriage.
    const bucket = worstTriage(held.filter(p => p.agent));
    // herdr's own count, shown whenever it exceeds what this client can serve: with
    // HERDR_SHELL_PANES off, a space of five panes drew one card and said nothing about the other four.
    const hint = w.paneCount > held.length ? ` (${w.paneCount})` : '';
    html += `<button class="chip${activeWorkspace === w.key ? ' active' : ''}${w.focused ? ' focused' : ''}"
      onclick="selectWorkspace('${escapeAttr(w.key)}')" data-ws-key="${escapeAttr(w.key)}" data-ws-name="${escapeAttr(w.name)}"
      title="${escapeAttr(w.focused ? w.name + ' — herdr is here' : w.name)}">`
      + (bucket ? `<span class="dot chip-dot" style="background:${TRIAGE_COLOR[bucket]}"></span>` : '')
      + `${escapeHtml(w.name)}${hint}</button>`;
  }
  return html + `</div>`;
}

function tabStrip(rows) {
  const wsTabs = tabRows(activeWorkspace);
  let html = `<div class="chip-strip" data-strip="tabs"><span class="chip-label">Tabs</span>`;
  if (wsTabs.length > 1) {
    html += `<button class="chip${!activeTab ? ' active' : ''}" onclick="selectTab(null)">All</button>`;
  }
  for (const t of wsTabs) {
    const bucket = worstTriage(agents.filter(a => agentTabKey(a) === t.key));
    html += `<button class="chip${activeTab === t.key ? ' active' : ''}${t.focused ? ' focused' : ''}"
      onclick="selectTab('${escapeAttr(t.key)}')" data-tab-key="${escapeAttr(t.key)}" data-tab-name="${escapeAttr(t.name)}"
      title="${escapeAttr(t.focused ? t.name + ' — herdr is here' : t.name)}">`
      + (bucket ? `<span class="dot chip-dot" style="background:${TRIAGE_COLOR[bucket]}"></span>` : '')
      + `${escapeHtml(t.name)}</button>`;
  }
  html += `<button class="chip chip-add" onclick="createTab()" title="New tab here">+</button>`;
  return html + `</div>`;
}

function emptyStateHtml() {
  if (switchingSession) return '<div class="empty">switching…</div>';
  // Only meaningful with exactly one source: with remotes configured,
  // "local" being idle says nothing about why the list is empty overall.
  const sources = sessionState?.sources || [];
  if (sources.length === 1) {
    const local = sources[0];
    const active = local.active || 'default';
    const entry = local.sessions.find(s => s.name === active);
    // Same wording herdr-remote-session prints, so CLI and UI agree.
    if (entry && !entry.running) {
      return `<div class="empty">session '${active}' is not running</div>`;
    }
  }
  return '<div class="empty">Waiting for agents…</div>';
}

function bindLongPressHandlers() {
  // Bind to spaces and tabs
  document.querySelectorAll('[data-ws-key]').forEach(el => {
    setupLongPress(el, 'workspace', el.dataset.wsKey, el.dataset.wsName);
  });
  document.querySelectorAll('[data-tab-key]').forEach(el => {
    setupLongPress(el, 'tab', el.dataset.tabKey, el.dataset.tabName);
  });
  // Bind to agents
  document.querySelectorAll('[data-pane-id]').forEach(el => {
    const id = el.dataset.paneId;
    const name = el.dataset.agentName;
    setupLongPress(el, 'agent', id, name);
  });
}

function selectWorkspace(key) { activeWorkspace = key; activeTab = null; render(); }

let sessionState = null;

function renderSessionSelector() {
  const btn = document.getElementById('sessionSelector');
  const menu = document.getElementById('sessionMenu');
  const picker = document.getElementById('sessionPicker');
  const sources = sessionState?.sources || [];
  const total = sources.reduce((n, s) => n + s.sessions.length, 0);
  // Nothing to switch between: stay out of the way. Hide the wrapper, not just
  // the button, or the header's flex gap leaves a stray space behind.
  if (total < 2) { picker.style.display = 'none'; menu.style.display = 'none'; return; }
  picker.style.display = '';

  const multiSource = sources.length > 1;
  if (multiSource) {
    // The trigger used to show local's session unconditionally, so with
    // remotes configured switching a remote looked like it did nothing.
    // Stay neutral instead of naming one specific source's session.
    btn.textContent = 'sessions ▾';
  } else {
    const local = sources[0];
    const active = local.active || 'default';
    const entry = local.sessions.find(s => s.name === active);
    // Fall back to the raw active value when nothing matches, so a
    // mismatch (e.g. herdr's default session not literally named
    // "default") degrades to a correct-but-unticked label rather than a
    // confidently wrong one.
    btn.textContent = (entry ? entry.name : local.active) + ' ▾';
  }

  const rowTargets = [];
  menu.innerHTML = sources.map(src => {
    const header = multiSource
      ? `<div class="chip-label" style="padding:4px 8px">${escapeHtml(src.host)}</div>`
      : '';
    const rows = src.sessions.map(s => {
      const active = (src.active || 'default') === s.name;
      const dim = s.running ? '' : 'opacity:0.55;';
      rowTargets.push({ host: src.host, session: s.name });
      // The tick lives in a fixed-width span so ticked and unticked names
      // start at the same x. A bare space would collapse in HTML.
      return `<div role="option" aria-selected="${active}" tabindex="0" style="${dim}"`
           + `><span class="session-mark">${active ? '✓' : ''}</span>`
           + `${escapeHtml(s.name)}${s.running ? '' : ' ·'}</div>`;
    }).join('');
    return header + rows;
  }).join('');

  // Host/session go on via the dataset property setter, not string
  // interpolation, so a name or host containing quotes or markup can't
  // break out of an attribute or inject JS. Wired after innerHTML is set,
  // since the rows above carry no onclick/onkeydown of their own.
  menu.querySelectorAll('[role="option"]').forEach((row, i) => {
    row.dataset.host = rowTargets[i].host;
    row.dataset.session = rowTargets[i].session;
    const activate = () => switchHerdrSession(row.dataset.host, row.dataset.session);
    row.addEventListener('click', activate);
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
    });
  });
}

function toggleSessionMenu() {
  const menu = document.getElementById('sessionMenu');
  // Read the computed style, not the inline one: the menu starts hidden via
  // the stylesheet, so menu.style.display is '' on the first click and an
  // inline-only check would silently "close" an already-closed menu.
  const show = getComputedStyle(menu).display === 'none';
  menu.style.display = show ? 'block' : 'none';
  document.getElementById('sessionSelector').setAttribute('aria-expanded', String(show));
}

let switchingSession = false;

function switchHerdrSession(host, session) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  document.getElementById('sessionMenu').style.display = 'none';
  document.getElementById('sessionSelector').setAttribute('aria-expanded', 'false');
  // A switch clears relay state and re-polls; a bare empty list would read as
  // breakage. Routed through the existing render path, not by poking the DOM —
  // there is no #agentList element. Mutate local state only after we know
  // the send will happen, so a dead socket can't leave the UI stuck showing
  // "switching…" forever.
  switchingSession = true;
  agents = [];
  render();
  ws.send(JSON.stringify({ type: 'session_switch', host, session }));
}

function backToWorkspaces() { activeWorkspace = null; activeTab = null; render(); }
function selectTab(key) { activeTab = key; render(); }

// A key is `host|id`; the relay wants those apart, since the id alone does not say which machine.
function splitKey(key) {
  const cut = (key || '').indexOf('|');
  return cut < 0 ? {host: 'local', id: key || ''} : {host: key.slice(0, cut), id: key.slice(cut + 1)};
}

function createTab() {
  if (!ws || !activeWorkspace) return;
  const {host, id} = splitKey(activeWorkspace);
  if (window.cue) cue('sparkle');
  ws.send(JSON.stringify({type:'create_tab', workspace_id: id, host}));
  setTimeout(() => { activeTab = null; }, 1500);
}

// Move herdr's own focus to whatever was long-pressed. Which id field is sent tells the relay what
// to focus, so a pane, a tab and a space all go through the one message.
function focusItem() {
  if (!contextTarget || !ws) return;
  const {type, id} = contextTarget;
  const {host, id: bare} = type === 'agent' ? {host: '', id} : splitKey(id);
  const field = type === 'agent' ? 'pane_id' : type === 'tab' ? 'tab_id' : 'workspace_id';
  ws.send(JSON.stringify({type: 'focus', [field]: bare, ...(host ? {host} : {})}));
  if (window.cue) cue('toggle');
  hideContextMenu();
}

