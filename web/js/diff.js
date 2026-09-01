// ---------------------------------------------------------------- diffs
//
// The relay ships a file edit as `-`/`+`/context lines with no `@@` header, because Edit's two
// sides are fragments of a file and any line number would be relative to the fragment (see
// transcript.py). A jump between hunks arrives as a bare `...` row.
function diffFragment(diff) {
  const block = document.createElement('div');
  block.className = 'diff';
  for (const line of String(diff ?? '').split('\n')) {
    const row = document.createElement('div');
    if (line === '...') { row.className = 'diff-line gap'; row.textContent = '⋯'; }
    else {
      const mark = line[0];
      row.className = 'diff-line ' + (mark === '+' ? 'add' : mark === '-' ? 'del' : 'ctx');
      // The leading marker is carried by the row's colour and its own gutter cell, so the text
      // keeps its real indentation instead of being shifted one column by a `+`.
      row.textContent = (mark === '+' || mark === '-' || mark === ' ') ? line.slice(1) : line;
      // The gutter is a fixed-width cell fed by `content: attr(data-mark)`, so a context
      // line leaves it empty rather than carrying a space character in the source.
      row.dataset.mark = mark === '+' ? '+' : mark === '-' ? '-' : '';
    }
    block.appendChild(row);
  }
  return block;
}

function handleMessage(msg) {
  if (msg.type === 'sessions') {
    sessionState = msg;
    renderSessionSelector();
    return;
  }
  if (msg.type === 'error') {
    // A rejected session_switch must not leave the UI stuck on
    // "switching…" forever. Minimum handling only: no toast UI, since
    // nothing else in this file renders errors.
    switchingSession = false;
    render();
    return;
  }
  if (msg.type === 'agents') {
    switchingSession = false;
    if (msg.spaces) spaces = msg.spaces;
    if (Array.isArray(msg.panes)) shellPanes = msg.panes;
    // Track timeline on status changes
    for (const a of msg.agents) {
      const prev = prevStatuses[a.pane_id];
      if (prev && prev !== a.status) {
        timeline.unshift({project: a.project, agent: a.agent, status: a.status, time: new Date()});
        if (timeline.length > 100) timeline.pop();
      }
      prevStatuses[a.pane_id] = a.status;
    }
    const previousAgents = new Map(agents.map(agent => [agent.pane_id, agent]));
    agents = msg.agents.map(agent => {
      const previous = previousAgents.get(agent.pane_id);
      if (agent.status !== 'blocked' || !previous) return agent;
      return {
        ...agent,
        prompt: previous.prompt,
        options: previous.options,
        multi_options: previous.multi_options,
        interaction: previous.interaction,
        multi: previous.multi,
        prompt_id: previous.prompt_id,
        selected_options: previous.selected_options,
      };
    });
    render();
  }
  else if (msg.type === 'agent_update') {
    const update = msg.agent;
    if (!update || !update.pane_id) return;
    const existing = agents.find(a => a.pane_id === update.pane_id);
    const previousStatus = existing?.status || prevStatuses[update.pane_id];
    if (existing) Object.assign(existing, update);
    else agents.push({...update});
    if (previousStatus && previousStatus !== update.status) {
      timeline.unshift({project: update.project, agent: update.agent, status: update.status, time: new Date()});
      if (timeline.length > 100) timeline.pop();
    }
    prevStatuses[update.pane_id] = update.status;
    render();
  }
  else if (msg.type === 'blocked') {
    const a = agents.find(x => x.pane_id === msg.pane_id);
    if (a) {
      a.status='blocked';
      a.prompt=msg.prompt;
      a.prompt_id=msg.prompt_id;
      a.options=msg.options;
      a.selected_options=msg.selected_options||[];
      a.multi_options=msg.multi_options||[];
      a.interaction=msg.interaction;
      a.multi=msg.multi;
    }
    else agents.push({...msg, status:'blocked'});
    if(window.cue) cue('chime');
    if (!msg.update) {
      timeline.unshift({project: msg.project, agent: msg.agent, status: 'blocked', time: new Date()});
      if (timeline.length > 100) timeline.pop();
    }
    render();
    if (msg.pane_id===activePane) openTerminal(activePane);
  } else if (msg.type === 'pane_content' && msg.pane_id === activePane) {
    if (msg.process && msg.process.name) {
      paneProcess[msg.pane_id] = msg.process;
      const shell = shellPane(msg.pane_id);
      if (shell) {
        document.getElementById('termTitle').textContent =
          `${shell.label||shell.project||shell.pane_id} · ${msg.process.name}`;
      }
    }
    const el = document.getElementById('termContent');
    // A read that was already in flight when the drag started, or a manual refresh: the tick's own
    // guard catches neither, and the line the reader is selecting inside can still be the line
    // mirrorPatch has to rewrite.
    if (selectionInside(el)) return;
    const prevHeight = el.scrollHeight;
    const prevScroll = el.scrollTop;
    // Nothing moved, so nothing here may move either -- not the DOM, and not the scroll, which the
    // fix-up below would otherwise yank to the bottom every 3s.
    if (!mirrorPatch(el, msg.content || '(empty)')) return;
    if (userScrolledUp) {
      // Keep scroll position relative to bottom (content may have grown at top)
      const growth = el.scrollHeight - prevHeight;
      el.scrollTop = prevScroll + growth;
    } else {
      el.scrollTop = el.scrollHeight;
    }
  } else if (msg.type === 'history' && msg.pane_id === activePane) {
    receiveHistory(msg);
  }
}

// A missing id must NOT reach the key as the string "undefined". It did, and everything downstream
// read `local|undefined` as a real space: splitKey handed back the truthy "undefined", render()'s
// `!k.endsWith('|')` guard let it through, and a relay that reports no hierarchy at all -- the demo
// worker, any relay older than `spaces` -- got one fabricated group per host, named after whichever
// project happened to be first, with its blocked agent buried inside it.
function spaceKey(host, id) { return `${host || 'local'}|${id || ''}`; }
function agentWorkspaceKey(a) { return spaceKey(a.host, a.workspace_id); }
function agentTabKey(a) { return spaceKey(a.host, a.tab_id); }

function workspaceRows() {
  if (spaces.workspaces.length) {
    // herdr's own list: the label the operator gave the space, the number they see on it, and the
    // full pane count -- including the shell panes the relay does not list, which is how a space
    // can legitimately show up here holding no agents at all.
    return spaces.workspaces.map(w => ({
      key: spaceKey(w.host, w.workspace_id), id: w.workspace_id, host: w.host || 'local',
      name: w.label || w.workspace_id, number: w.number || 0,
      focused: !!w.focused, paneCount: w.pane_count || 0,
    })).sort((a, b) => a.number - b.number);
  }
  // No hierarchy from this relay: group by the ids on the agents and name each space after a
  // pane's project, which is the best guess available.
  const seen = new Map();
  for (const a of agents) {
    if (!a.workspace_id) continue;
    const key = agentWorkspaceKey(a);
    if (!seen.has(key)) {
      seen.set(key, {key, id: a.workspace_id, host: a.host || 'local',
                     name: a.project || a.workspace_id, number: 0, focused: false, paneCount: 0});
    }
  }
  return [...seen.values()];
}

function tabRows(workspaceKey) {
  if (spaces.tabs.length) {
    return spaces.tabs
      .filter(t => spaceKey(t.host, t.workspace_id) === workspaceKey)
      .map(t => ({
        key: spaceKey(t.host, t.tab_id), id: t.tab_id, host: t.host || 'local',
        // A rename is a label that is not a bare integer -- NOT one that differs from `number`.
        // Live on this host: `wT:t4` has label "2" and number 4, because herdr's label is the tab's
        // POSITION in its space while the number is a separate counter. Comparing the two called
        // that a rename and rendered a heading reading `2` beside one reading `Tab 1`. And the
        // position is the right thing to show: it is what the desk shows.
        name: /^\d+$/.test((t.label || '').trim()) || !t.label ? `Tab ${t.label || t.number}` : t.label,
        number: t.number || 0, focused: !!t.focused, paneCount: t.pane_count || 0,
      }))
      .sort((a, b) => a.number - b.number);
  }
  const seen = new Map();
  agents.filter(a => agentWorkspaceKey(a) === workspaceKey).forEach((a) => {
    if (!a.tab_id || seen.has(agentTabKey(a))) return;
    seen.set(agentTabKey(a), {key: agentTabKey(a), id: a.tab_id, host: a.host || 'local',
                              name: a.label || `Tab ${seen.size + 1}`, number: seen.size + 1,
                              focused: false, paneCount: 0});
  });
  return [...seen.values()];
}

