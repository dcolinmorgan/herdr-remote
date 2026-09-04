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

// The two rows under the session header, both drawn from the snapshot already in hand.
//
// What replaced what: this was one flat row of the OTHER panes in the workspace, tagged `Tab` and
// `Space`, each chip named `label || pane_id`. On the host this was measured against, 28 of the 30
// panes carry no operator label -- so that row read `w6:pH  w6:pQ  w6:pR`, three chips whose names
// differ by one character and say nothing about what is inside them, with no mark for the pane you
// were actually in and no way to reach a tab by name. Now it is herdr's own two levels: the tabs of
// this space, then the panes of this tab, each named by what the pane can still say for itself.
//
// This row is rebuilt from EVERY `agents` snapshot -- that is how a terminal appearing beside the
// open pane shows up without a reopen -- and a rebuild costs it its scroll position: `replaceChildren`
// empties both groups, the row's scrollWidth collapses with them, and the browser clamps scrollLeft
// to 0. Then `scrollSibsToOpenPane` pulled the open chip back into view from there. So a reader who
// scrolled the row sideways to read the name of a pane at the far end was snapped back to their own
// pane every two seconds, which reads as a row that refuses to be moved.
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
  // Read BEFORE the rebuild: the two groups are about to be emptied, and the number is gone with
  // them. 0 while the session view is still display:none, where the assignment below is a no-op
  // and openTerminal's own call is what places the row.
  const kept = row ? row.scrollLeft : 0;
  const tabs = renderTabStrip(me, wsKey);
  const panes = renderPaneStrip(me, wsKey);
  // The separator is the boundary between the two levels, so it exists only when both are there --
  // a rule that hangs off what the two strips actually rendered rather than off a second count.
  const sep = document.getElementById('termSibSep');
  if (sep) sep.style.display = tabs && panes ? 'block' : 'none';
  if (row) row.style.display = tabs || panes ? 'flex' : 'none';
  if (!(tabs || panes)) { sibsAnchoredTo = null; return; }
  if (!row) return;
  row.scrollLeft = kept;
  anchorSibsToOpenPane();
}

// Places the row on the open pane, ONCE per pane. `sibsAnchoredTo` is what tells entering a session
// apart from the refresh that follows it every two seconds -- the first is allowed to move the row,
// the second is the reader's own scroll and is not. A row with no box cannot be measured, so it is
// left unanchored for the next caller that has one (openTerminal, after the view is displayed).
function anchorSibsToOpenPane() {
  const row = document.getElementById('termSibs');
  if (!row || !row.clientWidth || sibsAnchoredTo === activePane) return;
  sibsAnchoredTo = activePane;
  scrollSibsToOpenPane();
}

// One row holds both levels now, so it overflows sooner -- and the chip that must not be off screen
// is the one saying where you are. Computed rather than scrollIntoView(), which on a fixed-position
// ancestor also scrolls the document and takes the header with it.
function scrollSibsToOpenPane() {
  const row = document.getElementById('termSibs');
  // The PANE chip, not whichever marked chip comes first: the tab you are in is the leftmost thing
  // in the row and is never the one off screen, so matching it would report the job already done.
  const cur = row && (row.querySelector('#termSiblings [aria-current="true"]')
                      || row.querySelector('[aria-current="true"]'));
  if (!cur || row.style.display === 'none') return;
  const chip = cur.getBoundingClientRect(), box = row.getBoundingClientRect();
  if (chip.left < box.left) row.scrollLeft -= box.left - chip.left + 8;
  else if (chip.right > box.right) row.scrollLeft += chip.right - box.right + 8;
}

// Every pane in one space. By pane_id, not by array: the two lists are disjoint in a snapshot, but
// a `blocked` push can add an agent record for a pane still sitting in shellPanes, and that pane
// would draw two chips. One order for both strips, and creation order in practice: herdr numbers a
// pane's suffix p1, p2, ... p9, pA, pB, which sorts lexicographically into the order they opened.
function panesInSpace(wsKey) {
  if (!wsKey) return [];
  const seen = new Set(), list = [];
  for (const p of [...agents, ...shellPanes]) {
    if (seen.has(p.pane_id) || agentWorkspaceKey(p) !== wsKey) continue;
    seen.add(p.pane_id);
    list.push(p);
  }
  return list.sort((a, b) => a.pane_id.localeCompare(b.pane_id));
}

// The same panes, keyed by tab. Panes with no tab id are absent rather than pooled: a relay that
// reports no tabs has no level here to draw, and the pane row falls back to the whole space.
function panesByTab(wsKey) {
  const byTab = new Map();
  for (const p of panesInSpace(wsKey)) {
    if (!p.tab_id) continue;
    const key = agentTabKey(p);
    if (!byTab.has(key)) byTab.set(key, []);
    byTab.get(key).push(p);
  }
  return byTab;
}

// Where a tab chip lands you: the neediest agent in it, and failing that its first terminal. Ranked
// through `bucketOf` like everything else on this page, so the tap goes to the pane that would have
// been highest in the herd list -- if something in there is blocked, that is what you meant.
function tabLandingPane(list) {
  const rank = p => (p.agent ? TRIAGE_ORDER.indexOf(bucketOf(p)) : TRIAGE_ORDER.length);
  return [...list].sort((a, b) => rank(a) - rank(b) || a.pane_id.localeCompare(b.pane_id))[0];
}

// Returns whether it drew anything, which is what tells the shared row whether it has to exist.
function renderTabStrip(me, wsKey) {
  const strip = document.getElementById('termTabs');
  if (!strip) return false;
  strip.replaceChildren();
  const byTab = panesByTab(wsKey);
  // A tab with no pane we can name is not a place we can go -- there is no CLI for switching the
  // web client to an empty tab, and a chip that does nothing is worse than no chip. This is also
  // what keeps the row honest with HERDR_SHELL_PANES off: it becomes the tabs holding agents.
  const rows = tabRows(wsKey).filter(t => (byTab.get(t.key) || []).length);
  // One tab is not a choice, and 6 of the 10 agent panes measured live in a single-tab space --
  // they would each have paid a chip to be told the name of the only tab there is.
  if (rows.length < 2) { strip.style.display = 'none'; return false; }
  const mine = me && me.tab_id ? agentTabKey(me) : null;
  strip.append(...rows.map(t => tabChip(t, byTab.get(t.key), t.key === mine)));
  strip.style.display = 'flex';
  return true;
}

function tabChip(row, list, current) {
  const chip = document.createElement('button');
  chip.className = 'term-sib sib-tab';
  chip.dataset.sibTab = row.id;
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

function renderPaneStrip(me, wsKey) {
  const strip = document.getElementById('termSiblings');
  if (!strip) return false;
  strip.replaceChildren();
  // No tab id on the open pane means this relay reports no tabs at all; fall back to the whole
  // space, which is the set this row used to show and still the only one available.
  const list = me && me.tab_id ? (panesByTab(wsKey).get(agentTabKey(me)) || [])
                               : panesInSpace(wsKey);
  // Nothing to switch to means nothing to anchor either, so the row costs nothing -- 3 of the 10
  // agent panes measured have no pane beside them.
  if (list.length < 2) { strip.style.display = 'none'; return false; }
  strip.append(...list.map(p => siblingChip(p, p.pane_id === activePane)));
  strip.style.display = 'flex';
  return true;
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

