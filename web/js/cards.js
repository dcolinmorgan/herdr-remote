// ---- What a pane row is CALLED ---------------------------------------------
//
// Two questions, not one, so two functions and an explicit scope rather than one function guessing
// from whatever heading happens to be above it.
//
// In the herd the title line carries the two things that LOCATE a piece of work -- the space and the
// tab -- because that is what is unknown there. In a space's own view both are already established
// by the heading above the card, so repeating them says nothing, and worse: two panes in one tab
// would become indistinguishable, since the pane's own name is the only thing telling them apart.

/** The pane's own name: the operator's label, else the pane id.
 *
 *  Deliberately NOT `project`, which the relay sets to `basename(cwd)` -- a space's panes nearly all
 *  share it, so on a real host every card in `tmp-workspace` was called `tmp-workspace`, and so was
 *  the heading above them. The id is the only field that always separates two siblings.
 *
 *  Also what `rename_agent` is prefilled with and what the row announces, so those cannot drift from
 *  what the card shows. */
function paneName(p) { return p.label || p.pane_id; }

/** The cwd, but only when it says something the space label does not.
 *
 *  A space is almost always named after its directory, so this line spent itself repeating line one:
 *  `herdr-remote-dev` above `app-tools/herdr-remote-dev`, on row after row. Dropping it when the
 *  directory's own name matches keeps the path for exactly the case that carries information -- a
 *  pane sitting somewhere OTHER than the space root, in a worktree or a subdirectory. */
function informativeCwd(p, project) {
  if (!p.cwd) return null;
  if (baseName(p.cwd).toLowerCase() === (project || '').trim().toLowerCase()) return null;
  return shortCwd(p);
}

function shortCwd(p) { return p.cwd ? p.cwd.split('/').slice(-2).join('/') : null; }
function baseName(path) { return (path || '').split('/').filter(Boolean).pop() || ''; }

/** Herd scope: the parts, UNJOINED, because at 390px they must not truncate as one string.
 *
 *  Every pane in one project begins `herdr-remote-dev · `, so tail-truncating the joined title eats
 *  the tab name and leaves every row reading `herdr-remote-dev · d…` -- the characters that survive
 *  are the ones every row shares. Separate spans let the PROJECT give up width first and the tab --
 *  the only discriminator -- survive.
 *
 *  `project` is the SPACE's label, not `p.project`: the relay sets that to `basename(cwd)`, which is
 *  a per-pane fact and the very thing informativeCwd decides whether to show on line two. */
function paneParts(p) {
  const project = spaceNameByKey.get(agentWorkspaceKey(p)) || p.project || p.workspace_id || '';
  // A hand-set name first, then what the pane says it is doing. The title sits ahead of the cwd
  // because it is the only one of the three that tracks the work as it moves -- and in the herd this
  // exists to untangle (several agents in ONE project) the cwd is identical on every row, so it
  // discriminates nothing.
  const tabCount = tabCountBySpace.get(agentWorkspaceKey(p)) || 0;
  return {
    project,
    tab: meaningfulTabLabel(tabLabelByKey.get(agentTabKey(p)), tabCount),
    secondary: herdSecondary(p, project),
  };
}

/** Tab scope: the pane's own name leads, the cwd sits beneath. */
function paneTitleInTab(p) {
  return {primary: paneName(p), secondary: shortCwd(p)};
}

/** Line two in the herd, and the id is the last resort rather than an ornament: measured on this
 *  host, three agents share one tab of one space whose directory is the space's own name, so their
 *  label, their tab and their cwd are all empty or identical and the row would read
 *  `tuyaos-ai-qemu` three times. */
function herdSecondary(p, project) {
  return (p.label || p.title || '') || informativeCwd(p, project) || p.pane_id;
}

/** Line one, as spans. See paneParts for why it is not a string. */
function titleHtml(p, scope, host) {
  if (scope === 'tab') {
    return `<span class="pane-project">${escapeHtml(paneTitleInTab(p).primary)}</span>${host}`;
  }
  const parts = paneParts(p);
  return `<span class="pane-project">${escapeHtml(parts.project)}</span>`
    + (parts.tab ? `<span class="pane-sep"> · </span><span class="pane-tab">${escapeHtml(parts.tab)}</span>` : '')
    + host;
}

function secondaryOf(p, scope) {
  return (scope === 'tab' ? paneTitleInTab(p) : paneParts(p)).secondary;
}

function hostHtml(p) {
  return p.host && p.host !== 'local'
    ? `<span class="pane-host" style="color:var(--orange);font-size:0.6rem">@${escapeHtml(p.host)}</span>` : '';
}

function agentCard(a, scope) {
  // The dot is the BUCKET's colour, not the status's: `done` means two different things depending on
  // whether you have looked at it, and only the bucket knows which.
  const bucket = bucketOf(a);
  const pulseClass = a.status === 'working' ? ' pulse' : '';
  const named = paneName(a);
  const secondary = secondaryOf(a, scope);
  // The harness stays on line two. Collie can drop it because its rows carry an agent avatar; this
  // page has none, so the only place `claude` is written is here.
  const meta = [escapeHtml(a.agent), secondary ? escapeHtml(secondary) : '']
    .filter(Boolean).join(' · ');
  return `<div class="agent${bucket === 'needs' ? ' blocked' : ''}${bucket === 'ready' ? ' ready' : ''}${a.focused ? ' focused' : ''}" role="button" tabindex="0" aria-label="${escapeAttr(named)}, ${TRIAGE_META[bucket].label}${a.focused ? ', focused in herdr' : ''}" data-pane-id="${escapeAttr(a.pane_id)}" data-agent-name="${escapeAttr(named)}" data-bucket="${bucket}" onclick="openTerminal('${escapeAttr(a.pane_id)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openTerminal('${escapeAttr(a.pane_id)}')}">
    <span class="dot${pulseClass}" style="background:${TRIAGE_COLOR[bucket]}" aria-hidden="true"></span>
    <div class="info"><div class="project">${titleHtml(a, scope, hostHtml(a))}</div><div class="meta">${meta}</div></div>
    <span style="color:var(--muted);font-size:1.2rem" aria-hidden="true">›</span>
  </div>`;
}

function shellCard(p, scope) {
  // Same shape as an agent card so the list reads as one list, but a hollow dot rather than a fourth
  // shade of grey competing with Recent -- a terminal has no status to colour, and worstTriage says
  // the same thing by returning null for a set that holds only these.
  const named = paneName(p);
  const secondary = secondaryOf(p, scope);
  // The pane id, which within ONE workspace is the only thing that separates two of them: the 20
  // shell panes on this host collapse to 12 distinct cwd basenames inside their own spaces -- three
  // share a directory in wS, two in wE, because a workspace is usually one worktree. Dropped here
  // once it has become the card's own title, since printing it twice said it no better.
  const meta = [named === p.pane_id ? '' : p.pane_id, secondary || ''].filter(Boolean)
    .map(part => part === p.pane_id
      ? `<span style="font-family:monospace">${escapeHtml(part)}</span>` : escapeHtml(part))
    .join(' · ');
  return `<div class="agent shell${p.focused ? ' focused' : ''}" role="button" tabindex="0" data-shell="1"
    aria-label="${escapeAttr(named)}, terminal${p.focused ? ', focused in herdr' : ''}"
    data-pane-id="${escapeAttr(p.pane_id)}" data-agent-name="${escapeAttr(named)}"
    onclick="openTerminal('${escapeAttr(p.pane_id)}')"
    onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openTerminal('${escapeAttr(p.pane_id)}')}">
    <span class="dot dot-hollow" aria-hidden="true"></span>
    <div class="info"><div class="project">${titleHtml(p, scope, hostHtml(p))}</div><div class="meta">${meta}</div></div>
    <span style="color:var(--muted);font-size:1.2rem" aria-hidden="true">›</span>
  </div>`;
}

// The row under the session header: every pane in this space, in herdr's order, grouped by tab.
//
// What replaced what, twice. It was one flat row of the OTHER panes in the workspace, tagged `Tab`
// and `Space`, each chip named `label || pane_id` -- and on the host this was measured against, 28
// of the 30 panes carry no operator label, so that row read `w6:pH  w6:pQ  w6:pR`: three chips whose
// names differ by one character, no mark for the pane you were standing in, and no way to reach a
// tab by name. That became herdr's own two levels, the tabs of the space beside the panes of the
// current tab -- which put a WIDTH FIGHT in a 370px row. Two tabs cost 135px of it and three cost
// 253px, 68%, against a pane chip that can be 178px on its own, so a multi-tab space starved the
// level the reader actually came for. Splitting the row into two scrollers to keep the tab level
// from scrolling away only moved the fight; capping the tabs at 45% only rationed it.
//
// So there is ONE list and one scroller, read left to right the way a multiplexer's own status line
// is: the panes of tab 1, then the panes of tab 2, in tab order, each group led by its tab's name.
// Every pane in the space is one tap away instead of two (a tab chip, then whatever pane it decided
// to land you on), and a pane chip gets the whole row's width when it needs it.
//
// THE TAB'S NAME IS STICKY, which is what makes one row safe. The names sit inside the flow, so at
// rest the row simply reads `Tab 1  p1 p2 | ble-dev  p3 p4`; once a group's panes scroll under the
// left edge its name stays pinned there, and the next group's name replaces it on the way past. That
// is the bug the two-scroller version existed to fix -- in one shared scroller the scroll that
// reveals the open pane drove the tab level off the left edge, and at 390px in a 3-tab space what
// was left standing there was a SIBLING tab, `ble-verify` while you were in `ble-dev`, reading as
// the breadcrumb of the tab you were in. A pinned name cannot be the wrong one: it belongs to the
// panes beside it.
//
// Colour is deliberately NOT the channel. The dot is the triage bucket (red -> orange -> green ->
// grey) and a filled chip is the selection, everywhere on this page; a third scale keyed to "which
// tab" would collide with both. The name says it, the 1px rule between groups says where one ends,
// and both cost less than a hue.
//
// The row is rebuilt from EVERY `agents` snapshot -- that is how a terminal appearing beside the
// open pane shows up without a reopen -- and a rebuild costs it its scroll position:
// `replaceChildren` empties it, its scrollWidth collapses, and the browser answers by clamping
// scrollLeft to 0. Then the auto-scroll pulled the open chip back into view from there. So a reader
// who scrolled sideways to read the name of a pane at the far end was snapped back to their own pane
// every two seconds, which reads as a row that refuses to be moved.
//
// The offset is the reader's, so it survives a rebuild they did not ask for, and the auto-scroll
// fires only when the row is NEW to this pane. `sibsAnchoredTo` is what tells that apart from a
// refresh, and it is dropped in the two places that mean "new": openTerminal's real-switch branch
// (which covers entering a session from the list, since activePane is null there) and the row
// disappearing entirely. A re-entry -- openTerminal on the pane already in front of you, which
// every `blocked` event does -- clears nothing, so a question arriving cannot drag the row back.
let sibsAnchoredTo = null;

function renderSiblings() {
  const me = paneById(activePane);
  // No workspace id means this relay reports no hierarchy at all, and every pane would look like a
  // sibling of every other one. Nothing is better than a wrong neighbourhood.
  const wsKey = me && me.workspace_id ? agentWorkspaceKey(me) : null;
  const row = document.getElementById('termSibs');
  const strip = document.getElementById('termSiblings');
  // Read BEFORE the rebuild: the strip is about to be emptied and the number goes with it. 0 while
  // the session view is still display:none, where the assignment below is a no-op and openTerminal's
  // own call is what places the row.
  const kept = strip ? strip.scrollLeft : 0;
  const drew = renderSibStrip(me, wsKey);
  if (row) row.style.display = drew ? 'flex' : 'none';
  if (!drew) { sibsAnchoredTo = null; return; }
  if (!strip) return;
  strip.scrollLeft = kept;
  anchorSibsToOpenPane();
}

/** Every pane in the space, grouped by tab. Returns whether it drew anything, which is what tells
 *  the row whether it has to exist at all.
 *
 *  Two panes is the threshold, and it is about the SPACE rather than the tab: with one pane in the
 *  space there is nothing to switch to, and the row would cost the output 33px to say so. A tab with
 *  no pane this client can name draws no group -- there is no CLI for pointing the web client at an
 *  empty tab -- which is also what keeps the row honest with HERDR_SHELL_PANES off, where it becomes
 *  the tabs that hold agents. */
function renderSibStrip(me, wsKey) {
  const strip = document.getElementById('termSiblings');
  if (!strip) return false;
  strip.replaceChildren();
  const all = panesInSpace(wsKey);
  if (all.length < 2) return false;
  const groups = groupPanesByTab(wsKey).filter(g => g.panes.length);
  // One tab is not a choice, and 6 of the 10 agent panes measured sit in a single-tab space -- they
  // would each have paid a chip, and a rule, to be told the name of the only tab there is.
  if (groups.length < 2) {
    strip.append(...all.map(p => siblingChip(p, p.pane_id === activePane)));
    return true;
  }
  const mine = me && me.tab_id ? agentTabKey(me) : null;
  for (const g of groups) strip.append(sibGroup(g, g.key === mine));
  return true;
}

// One tab's panes, behind its name. A group is a box of its own so the name can stick INSIDE it: a
// sticky element is held by its containing block, so each name is pinned only while its own panes
// are on screen and is pushed out by the next group rather than stacking up at the edge.
function sibGroup(g, current) {
  const box = document.createElement('div');
  box.className = 'sib-group';
  box.dataset.sibGroup = g.key;
  box.append(tabChip(g, g.panes, current),
             ...g.panes.map(p => siblingChip(p, p.pane_id === activePane)));
  return box;
}

// Places the row on the pane you are in, ONCE per pane. `sibsAnchoredTo` is what tells entering a
// session apart from the refresh that follows it every two seconds -- the first is allowed to move
// the row, the second is the reader's own scroll and is not. A row with no box cannot be measured,
// so it is left unanchored for the next caller that has one (openTerminal, after the view is shown).
function anchorSibsToOpenPane() {
  const row = document.getElementById('termSibs');
  if (!row || !row.clientWidth || sibsAnchoredTo === activePane) return;
  sibsAnchoredTo = activePane;
  scrollSibsToOpenPane();
}

/** Puts the pane you are in on the screen, with its tab's name if the two fit.
 *
 *  Computed rather than scrollIntoView(), which on a fixed-position ancestor also scrolls the
 *  document and takes the header with it. Two things make it more than a reveal:
 *
 *  - A stuck name is PAINTED at the left edge rather than where it sits in the row, so its rect is
 *    no use as a flow position. The group box never sticks, and that is what the arithmetic uses.
 *  - A pane placed hard against the left edge would sit UNDER the stuck name. So either the group's
 *    name fits in the flow beside it -- the common case, since a name leads its own tab's panes
 *    rather than following every tab in the space -- or the pane is placed to the right of the name's
 *    own width. */
function scrollSibsToOpenPane() {
  const strip = document.getElementById('termSiblings');
  if (!strip || !strip.clientWidth) return;
  const pane = strip.querySelector('[data-sib-id][aria-current="true"]');
  if (!pane) return;
  const group = pane.closest('.sib-group');
  const label = group && group.querySelector('.sib-tab');
  const box = strip.getBoundingClientRect(), view = strip.clientWidth;
  const at = el => el.getBoundingClientRect().left - box.left + strip.scrollLeft;
  const start = at(pane), end = start + pane.getBoundingClientRect().width;
  const lead = label ? label.getBoundingClientRect().width + 6 : 0;
  const together = group && end - at(group) <= view;
  // The window of offsets that satisfies both, and the reader's own offset is left alone inside it.
  const lo = end - view + 8;
  const hi = together ? at(group) - 8 : start - lead - 8;
  strip.scrollLeft = Math.max(0, Math.min(Math.max(strip.scrollLeft, lo), Math.max(lo, hi)));
}

/** Where a pane sits in herdr's own `pane list`, which is the order it sits in on the screen the
 *  operator is looking at -- the same split-tree walk `pane layout` returns, top-left first.
 *
 * Neither of the two things this page could compute for itself is that order. The relay splits ONE
 * `pane list` into `agents` and `panes`, so the array a pane arrives in says nothing about where it
 * is: merging them puts every agent ahead of every terminal, which misplaces the terminal sitting
 * between two agents. And sorting the ids -- what this did -- is worse, because the suffix is a
 * creation counter in a base wider than ten: measured live, `w6:t1` reads pH, p15, p12 at the desk
 * and sorted to p12, p15, pH, while `w6:tC` reads p16, p18, p17 and sorted to p16, p17, p18. Even a
 * correct creation order would not be it, since `pane swap` and `pane move` exist.
 *
 * Missing means last, and the sort is stable: a relay older than `order` sends none at all, so
 * every pane ties and both strips keep the order the relay sent. The one pane that can lack it on a
 * relay that does send it is an agent a `blocked` push appended, and the next snapshot -- two
 * seconds -- puts it back where it belongs. */
function paneOrder(p) {
  return typeof p.order === 'number' ? p.order : Number.MAX_SAFE_INTEGER;
}

// Every pane in one space, in herdr's order. By pane_id, not by array: the two lists are disjoint in
// a snapshot, but a `blocked` push can add an agent record for a pane still sitting in shellPanes,
// and that pane would draw two chips.
function panesInSpace(wsKey) {
  if (!wsKey) return [];
  const seen = new Set(), list = [];
  for (const p of [...agents, ...shellPanes]) {
    if (seen.has(p.pane_id) || agentWorkspaceKey(p) !== wsKey) continue;
    seen.add(p.pane_id);
    list.push(p);
  }
  return list.sort((a, b) => paneOrder(a) - paneOrder(b));
}

// Where a tab chip lands you: the neediest agent in it, and failing that its first terminal. Ranked
// through `bucketOf` like everything else on this page, so the tap goes to the pane that would have
// been highest in the herd list -- if something in there is blocked, that is what you meant.
function tabLandingPane(list) {
  const rank = p => (p.agent ? TRIAGE_ORDER.indexOf(bucketOf(p)) : TRIAGE_ORDER.length);
  // Ties go to whichever comes first at the desk, not to whichever id sorts first -- the same
  // question the strip's own order asks, so the tap lands on the chip a reader would have picked.
  return [...list].sort((a, b) => rank(a) - rank(b) || paneOrder(a) - paneOrder(b))[0];
}

function tabChip(row, list, current) {
  const chip = document.createElement('button');
  chip.className = 'term-sib sib-tab';
  // Absent rather than the string "undefined" for the trailing `…` group, whose panes name a tab
  // `tab list` has not caught up with -- the poll race right after a create.
  if (row.id) chip.dataset.sibTab = row.id;
  if (current) chip.setAttribute('aria-current', 'true');
  // The same dot the space and tab chips in the herd carry, from the same classifier -- so a tab
  // says what is going on inside it before you open it. Null for a tab holding only terminals,
  // which is `worstTriage` declining to invent a fifth shade for a pane that has no status.
  const bucket = worstTriage(list.filter(p => p.agent));
  const dot = document.createElement('span');
  dot.className = bucket ? 'dot' : 'dot dot-hollow';
  if (bucket) dot.style.background = TRIAGE_COLOR[bucket];
  const name = document.createElement('span');
  name.className = 'sib-name';
  name.textContent = row.name;
  chip.append(dot, name);
  chip.setAttribute('aria-label', `Tab ${row.name}, ${list.length} pane${list.length === 1 ? '' : 's'}`);
  // Tapping the tab you are already in would be a no-op switch; leave it inert rather than
  // re-entering openTerminal and closing the panels you have open.
  if (!current) chip.onclick = () => openTerminal(tabLandingPane(list).pane_id);
  return chip;
}

/** What a chip is called.
 *
 * `label || pane_id` degenerated to the pane id for 28 of the 30 panes on the measured host, so this
 * falls through Collie's order (paneDisplayName) instead: the operator's label, then what the pane
 * itself is saying it is doing, then what it is. The pane id's suffix rides along as a tag either
 * way, because a name that repeats across tabmates -- three shells in `herdr`, three claudes in one
 * tab -- still needs something that does not. `project` is never in here: the relay sets it to
 * basename(cwd), which by construction every pane in one worktree shares. */
function paneChipName(p) {
  // `title` is herdr's terminal title with the harness banner stripped (activity_title), so it is
  // there while an agent is working -- 2 of the 10 agent panes measured, both saying something real
  // -- and empty when it is idle or done. A `panes` entry has no such field at all.
  return p.label || (p.agent ? (p.title || p.agent) : (baseName(p.cwd) || 'shell'));
}

function siblingChip(p, current) {
  // A `panes` entry has no `agent` field at all; an agent entry always does. Same test the card
  // renderers use, one array apart.
  const shell = !p.agent;
  const chip = document.createElement('button');
  chip.className = 'term-sib';
  chip.dataset.sibId = p.pane_id;
  chip.dataset.sibShell = shell ? '1' : '0';
  if (current) chip.setAttribute('aria-current', 'true');
  const dot = document.createElement('span');
  dot.className = shell ? 'dot dot-hollow' : 'dot';
  // The bucket's colour, not the status's -- the same thing the card and the section header say,
  // through the same classifier, so the strip needs no legend of its own.
  if (!shell) dot.style.background = TRIAGE_COLOR[bucketOf(p)];
  const name = document.createElement('span');
  name.className = 'sib-name';
  name.textContent = paneChipName(p);
  name.title = name.textContent;   // the CSS clips it at 32vw; this is where the rest of it lives
  const tail = document.createElement('span');
  tail.className = 'sib-tag';
  tail.textContent = p.pane_id.split(':').pop();
  chip.append(dot, name, tail);
  chip.setAttribute('aria-label',
    `${shell ? 'Terminal' : 'Agent'} ${name.textContent} ${tail.textContent}${current ? ', open' : ''}`);
  if (!current) chip.onclick = () => openTerminal(p.pane_id);
  return chip;
}

// A switch is not a refresh. The read for the pane you just picked is a relay round trip away --
// milliseconds on this host, an SSH hop and up to seconds on a remote one -- and until it lands the
// mirror on screen is the output of the pane you LEFT, under the new pane's title and beside its
// filled chip. That is the reported lag: the labels moved and the content did not, with nothing
// saying it was stale. So a switch empties the mirror and says so, and the first read fills it.
function clearPaneMirror() {
  const el = document.getElementById('termContent');
  if (!el) return;
  // A range left inside the old output would make the `pane_content` handler refuse to render the
  // new pane's first read -- selectionInside guards the mirror and cannot tell a stale range from a
  // live one -- and a caret would survive into a buffer it never pointed at. Both are anchored in
  // text that is about to stop existing, so both go. Only ever OUR text: a selection anywhere else
  // on the page is the reader's and is none of this function's business.
  const sel = window.getSelection && window.getSelection();
  if (sel && sel.rangeCount && el.contains(sel.anchorNode)) sel.removeAllRanges();
  el.__mirror = null;   // mirrorPatch reconciles against this, and it now describes another pane
  el.replaceChildren(document.createTextNode('Loading…'));
  el.scrollTop = 0;
}

function openTerminal(paneId) {
  // Re-entered on every blocked event for the pane already open, so only a real switch closes these
  // -- the history panel belongs to one conversation, and the search holds the OTHER pane's output
  // in `originalContent`. Leaving the search open across a switch (which the sibling chips made a
  // one-tap move) meant the next keystroke restored pane A's output into pane B's session.
  // The reading state goes with them, for the same reason: a re-entry is a title refresh, and
  // resetting it on one would drop a reader who had paged back to the live screen the moment
  // their agent asked a question -- the one moment they are most likely to be reading.
  if (activePane !== paneId) {
    hideHistory(); hideSearch(); clearPaneMirror();
    paneLines = PANE_LINES_BASE; paneFollowing = true; userScrolledUp = false;
    // A real switch is one of the two moments the sibling row may place itself; dropping the anchor
    // here is what asks for it, and what keeps a re-entry from asking again.
    sibsAnchoredTo = null;
  }
  activePane = paneId;
  const a = agents.find(x => x.pane_id===paneId);
  const shell = shellPane(paneId);
  // A terminal is named by its id, because its directory usually is not unique on the host. The
  // process name lands here once the read comes back (see paneTitle).
  document.getElementById('termTitle').textContent = a
    ? `${a.label||a.workspace_label||a.project} · ${a.agent}`
    : shell ? `${shell.label||shell.project||shell.pane_id} · ${shell.pane_id}` : paneId;
  // A pane that names no agent session has no transcript to show. `false` only -- undefined means
  // the relay doesn't report it, and the button stays. A terminal never has one.
  const historyBtn = document.querySelector('.history-btn');
  if (historyBtn) historyBtn.style.display = (shell || (a && a.has_session === false)) ? 'none' : '';
  renderSiblings();
  document.getElementById('agentListView').style.display = 'none';
  if (refreshInterval) clearInterval(refreshInterval);
  document.getElementById('terminalView').classList.add('active');
  // AFTER the view is displayed: renderSiblings ran while it was still display:none, where every
  // rect is zero, no chip can be found to be off screen, and scrollLeft cannot be written either --
  // so it deliberately left the row unanchored for this call to place. Guarded the same way, since
  // openTerminal is re-entered on every `blocked` event for the pane already in front of you and a
  // question arriving must not drag the row back under the reader's thumb.
  anchorSibsToOpenPane();
  navPush('terminal', hideTerminal);
  const qa = document.getElementById('quickActions');
  const ak = document.getElementById('actionKeys');
  qa.replaceChildren();
  ak.replaceChildren();
  if (a&&a.status==='blocked') {
    const opts = a.interaction==='omp_question'&&a.multi
      ? (Array.isArray(a.multi_options)?a.multi_options:[])
      : (Array.isArray(a.options)?a.options:[]);
    for (const option of opts) {
      const button = document.createElement('button');
      const lower = option.toLowerCase();
      button.className = lower.includes('yes')||lower.includes('approve')?'btn-yes':lower.includes('trust')?'btn-trust':'btn-no';
      button.textContent = option.split(',')[0];
      if (a.interaction==='omp_question'&&a.multi) {
        button.dataset.selected=String((a.selected_options||[]).includes(option));
        button.classList.toggle('selected',button.dataset.selected==='true');
        button.addEventListener('click',()=>{
          const selected=button.dataset.selected!=='true';
          button.dataset.selected=String(selected);
          button.classList.toggle('selected',selected);
          ws.send(JSON.stringify({type:'question_toggle',pane_id:activePane,prompt_id:a.prompt_id,option}));
        });
      } else {
        button.addEventListener('click',()=>respond(option));
      }
      qa.appendChild(button);
    }
    if (a.interaction==='omp_question'&&a.multi) {
      const submit=document.createElement('button');
      submit.className='btn-yes';
      submit.textContent='Submit';
      submit.addEventListener('click',()=>ws.send(JSON.stringify({type:'question_submit',pane_id:activePane,prompt_id:a.prompt_id})));
      qa.appendChild(submit);
    } else if (a.interaction!=='omp_question'&&opts.includes('yes, single permission')) {
      for (const [label,cls,response] of [
        ['y','key-green','yes, single permission'],
        ['a','key-blue','trust, always allow'],
        ['n','key-red','no (tab to edit)'],
      ]) {
        const button = document.createElement('button');
        button.className = cls;
        button.textContent = label;
        button.addEventListener('click',()=>respond(response));
        ak.appendChild(button);
      }
    }
  }
  refreshPane();
  refreshInterval = setInterval(mirrorTick, 3000);
}

