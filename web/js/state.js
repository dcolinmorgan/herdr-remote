
let ws = null, agents = [], activePane = null, refreshInterval = null, userScrolledUp = false;
// The panes with no agent in them, from the relay's `panes` array. Empty unless the relay was
// started with HERDR_SHELL_PANES -- a client cannot tell "switched off" from "none here", and
// does not need to: both mean there is nothing to show.
let shellPanes = [];
// What is running in the pane currently open, once it has been asked for. pane_id -> {name,cmdline}
let paneProcess = {};

function shellPane(id) { return shellPanes.find(p => p.pane_id === id); }
// One lookup for both kinds. Agents win: a pane_id is unique per host and the two lists are
// disjoint, but if a pane gains an agent between polls the agent record is the fuller one.
function paneById(id) { return agents.find(a => a.pane_id === id) || shellPane(id); }
let timeline = [], prevStatuses = {};
// Selections are host-qualified keys, not bare ids: every herdr numbers its own workspaces w1,
// w2, ... so on a relay watching two hosts an id alone picks out two different spaces.
let activeWorkspace = null;
let activeTab = null;
// The hierarchy as herdr reports it -- real labels, the operator's numbering, which space is
// focused, and how many panes each holds in total. Arrives with every `agents` broadcast; a relay
// that predates it (and the demo worker) sends none, and then the ids carried on the agents
// themselves are all there is to group by.
let spaces = {workspaces: [], tabs: []};

// Settings — auto-detect relay when served from same origin
const autoRelayUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host;
const DEMO_RELAY = 'wss://herdr-remote-demo.yyrzrh5wfg.workers.dev';
const isDemo = location.hostname.includes('herdr-demo.pages.dev');
const isSelfRelay = !isDemo && !location.hostname.includes('pages.dev') && !location.hostname.includes('localhost');
const savedUrl = localStorage.getItem('herdr_relay_url') || (isSelfRelay ? autoRelayUrl : (isDemo ? DEMO_RELAY : ''));
const urlToken = new URLSearchParams(location.search).get('token');
if (urlToken) localStorage.setItem('herdr_relay_token', urlToken);
const savedToken = localStorage.getItem('herdr_relay_token') || '';
document.getElementById('relayUrl').value = savedUrl;
document.getElementById('relayToken').value = savedToken;

// Appearance -- Auto / Light / Dark.
//
// The VISUAL switch is pure CSS and none of this code's business: every token is a `light-dark()`
// pair and the root's `color-scheme` picks the half, so Auto is correct with JavaScript disabled
// entirely. What is left here is the part CSS cannot do:
//   1. the pin attribute, in BOTH directions -- a stale `data-theme` has to come off, or Dark → Auto
//      leaves the page pinned dark until a reload;
//   2. the theme-color metas, which carry `media` attributes and so follow the OS rather than a pin.
// The <head> script has already stamped the attribute for a pinned reader before first paint; this
// is idempotent for that case and additionally fixes up the metas and the control.
const THEME_KEY = 'herdr_theme';
const THEME_META = {light: '#f5f5f5', dark: '#0a0a0a'};  // --bg's two halves, rasterized.

function themePref() {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    return raw === 'light' || raw === 'dark' ? raw : 'auto';
  } catch (e) { return 'auto'; }
}

function themeResolved(pref) {
  if (pref !== 'auto') return pref;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme() {
  const pref = themePref(), resolved = themeResolved(pref);
  if (pref === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = pref;
  // Pinned: hand BOTH metas the pinned colour, so whichever one the browser's own media query
  // selects is the right answer. On Auto: give each back its own half and let the query decide.
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    const own = (meta.getAttribute('media') || '').includes('dark') ? 'dark' : 'light';
    meta.content = THEME_META[pref === 'auto' ? own : resolved];
  }
  for (const btn of document.querySelectorAll('[data-theme-choice]'))
    btn.setAttribute('aria-checked', btn.dataset.themeChoice === pref ? 'true' : 'false');
  const note = document.getElementById('themeResolved');
  if (note) note.textContent = pref === 'auto' ? 'Now ' + resolved + '.' : '';
}

function setTheme(pref) {
  try {
    // Absent means Auto, so un-pinning REMOVES the key rather than storing a sentinel: the
    // pre-paint script's getItem then returns null and it does nothing, which is exactly right.
    if (pref === 'auto') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch (e) {}  // Private mode: the choice still holds for this session.
  applyTheme();
  if (window.cue) cue('toggle');
}

// Follow the OS while on Auto, so an evening switch lands without a reload. The CSS half handles
// itself; this keeps the metas and the control's readout honest.
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (themePref() === 'auto') applyTheme();
});
applyTheme();



// Back-button navigation — every view or overlay that covers the agent list
// pushes a history entry when it opens, so the Android system back button (and
// the browser's) dismisses the top layer instead of leaving the app.
const navStack = [];

function navPush(key, close) {
  if (navStack.some(l => l.key === key)) return;
  try { history.pushState({ herdrNav: key, depth: navStack.length + 1 }, ''); }
  catch (e) { return; } // no History API here — nothing to intercept
  navStack.push({ key, close });
}

// Dismiss a layer from the UI. Drops its history entry plus anything stacked on
// top, so the two stacks stay in sync and a repeat tap can't over-rewind.
function navClose(key, close) {
  const i = navStack.findIndex(l => l.key === key);
  if (i < 0) { close(); return; }
  const dropped = navStack.splice(i);
  history.go(-dropped.length);
  for (const l of dropped) l.close();
}

// System / browser back: close every layer above the entry we landed on.
addEventListener('popstate', () => {
  const depth = history.state && history.state.herdrNav ? history.state.depth : 0;
  while (navStack.length > depth) navStack.pop().close();
});

// What the panel is covering, so closing it restores that and not something else. The session
// view is `position: fixed; z-index: 50` over an opaque background, so a panel opened from inside
// a session used to render in normal flow UNDERNEATH it -- present, focusable, and invisible.
// Deactivating it is the fix; the header buttons stay reachable in a session only because the
// overlay's hardcoded 49px top edge sits above the header's real ~69px, which is how the panel
// was reachable-but-hidden in the first place.
let panelReturn = null;

function panelIsOpen() {
  return document.getElementById('settingsView').style.display === 'block'
      || document.getElementById('timelineView').style.display === 'block';
}

function openPanel(id) {
  // Only on the way IN: swapping between Settings and Timeline must not re-read this, because by
  // then the session view is already deactivated and the panel would forget to bring it back.
  if (!panelIsOpen()) {
    const term = document.getElementById('terminalView');
    panelReturn = term.classList.contains('active') ? 'terminal' : 'list';
    if (panelReturn === 'terminal') term.classList.remove('active');
  }
  document.getElementById('settingsView').style.display = id === 'settingsView' ? 'block' : 'none';
  document.getElementById('timelineView').style.display = id === 'timelineView' ? 'block' : 'none';
  document.getElementById('agentListView').style.display = 'none';
  navPush('panel', hidePanel);
}

function toggleSettings() {
  if (document.getElementById('settingsView').style.display === 'block') { closePanel(); return; }
  openPanel('settingsView');
  document.getElementById('settingsStatus').innerHTML = ws && ws.readyState === 1
    ? `<span style="color:var(--green)">● Connected</span> · ${agents.length} agents`
    : `<span style="color:var(--red)">● Disconnected</span>`;
  renderSessions();
}

function toggleTimeline() {
  if (document.getElementById('timelineView').style.display === 'block') { closePanel(); return; }
  openPanel('timelineView');
  renderTimeline();
}

// Settings and Timeline swap places over whatever they were opened from, so they share one
// history entry: switching between them doesn't cost an extra back press.
function closePanel() { navClose('panel', hidePanel); }
function hidePanel() {
  document.getElementById('settingsView').style.display = 'none';
  document.getElementById('timelineView').style.display = 'none';
  const toSession = panelReturn === 'terminal';
  panelReturn = null;
  // Restoring the agent list unconditionally used to leave it rendered under the still-active
  // session view; the session keeps polling while the panel is up, so it comes back current.
  document.getElementById('agentListView').style.display = toSession ? 'none' : '';
  if (toSession) document.getElementById('terminalView').classList.add('active');
}

function renderTimeline() {
  const el = document.getElementById('timelineList');
  if (!timeline.length) { el.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px">No activity yet. Status changes appear here.</p>'; return; }
  el.innerHTML = timeline.map(e => {
    const t = e.time.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    const color = e.status==='blocked'?'var(--red)':e.status==='working'?'var(--green)':'var(--muted)';
    return `<div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--border)"><span style="color:var(--muted)">${t}</span><span style="flex:1">${e.project} (${e.agent})</span><span style="color:${color}">${e.status}</span></div>`;
  }).join('');
}

function saveAndConnect() {
  const url = document.getElementById('relayUrl').value.trim();
  if (!url) return;
  localStorage.setItem('herdr_relay_url', url);
  const token = document.getElementById('relayToken').value.trim();
  if (token) localStorage.setItem('herdr_relay_token', token);
  else localStorage.removeItem('herdr_relay_token');
  connect();
  toggleSettings();
}

// Session switcher
function getSessions() {
  try { return JSON.parse(localStorage.getItem('herdr_sessions') || '[]'); }
  catch { return []; }
}

function saveSessions(sessions) {
  localStorage.setItem('herdr_sessions', JSON.stringify(sessions));
}

function saveCurrentSession() {
  const url = document.getElementById('relayUrl').value.trim();
  if (!url) { alert('Enter a relay URL first'); return; }
  const name = prompt('Session name:', new URL(url).hostname || 'Session');
  if (!name) return;
  const sessions = getSessions();
  const existing = sessions.findIndex(s => s.url === url);
  if (existing >= 0) {
    sessions[existing].name = name;
  } else {
    sessions.push({ name, url, token: document.getElementById('relayToken').value.trim() || '' });
  }
  saveSessions(sessions);
  renderSessions();
  if (window.cue) cue('success');
}

function switchSession(idx) {
  const sessions = getSessions();
  const s = sessions[idx];
  if (!s) return;
  document.getElementById('relayUrl').value = s.url;
  document.getElementById('relayToken').value = s.token || '';
  saveAndConnect();
}

function deleteSession(idx, e) {
  e.stopPropagation();
  const sessions = getSessions();
  if (confirm(`Delete "${sessions[idx]?.name}"?`)) {
    sessions.splice(idx, 1);
    saveSessions(sessions);
    renderSessions();
  }
}

function renderSessions() {
  const sessions = getSessions();
  const el = document.getElementById('sessionList');
  if (!sessions.length) {
    el.innerHTML = '<div style="font-size:0.75rem;color:var(--muted)">No saved sessions</div>';
    return;
  }
  const currentUrl = localStorage.getItem('herdr_relay_url') || '';
  el.innerHTML = sessions.map((s, i) => `
    <div class="session-item${s.url === currentUrl ? ' active' : ''}" onclick="switchSession(${i})">
      <span class="session-name">${s.name}</span>
      <span class="session-url">${new URL(s.url).hostname}</span>
      <button onclick="deleteSession(${i}, event)" aria-label="Delete" style="background:none;border:none;color:var(--muted);cursor:pointer;padding:4px">×</button>
    </div>
  `).join('');
}

// Connection
function connect() {
  let url = localStorage.getItem('herdr_relay_url') || (isSelfRelay ? autoRelayUrl : (isDemo ? DEMO_RELAY : ''));
  if (!url) { showSetup(); return; }
  if (ws) ws.close();
  setStatus('connecting');
  // Append token as query param if stored separately
  const token = localStorage.getItem('herdr_relay_token');
  let wsUrl = url;
  if (token) wsUrl += (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
  ws = new WebSocket(wsUrl);
  ws.onopen = () => { setStatus('connected'); if(window.cue) cue('ready'); };
  ws.onclose = () => { setStatus('disconnected'); setTimeout(connect, 3000); };
  ws.onerror = () => setStatus('disconnected');
  ws.onmessage = (e) => handleMessage(JSON.parse(e.data));
}

function setStatus(s) {
  const dot = document.getElementById('statusDot');
  const label = document.getElementById('connLabel');
  dot.style.background = s==='connected'?'var(--green)':s==='connecting'?'var(--orange)':'var(--red)';
  label.textContent = s==='connected'?'live':s==='connecting'?'connecting…':'offline';
  label.style.color = s==='connected'?'var(--green)':s==='connecting'?'var(--orange)':'var(--red)';
}

function showSetup() {
  document.getElementById('agents').innerHTML = `<div class="empty">
    <p style="font-size:1.5rem;margin-bottom:12px">🐑</p>
    <p><strong>herdr-remote</strong></p>
    <p style="margin-top:8px">Monitor & approve agents from your phone</p>
    <p style="margin-top:16px"><button onclick="tryDemo()" style="padding:10px 20px;border-radius:8px;border:none;background:var(--blue);color:var(--on-accent);font-weight:600;font-size:0.85rem;cursor:pointer">Try Demo</button></p>
    <p style="margin-top:16px;font-size:0.75rem;color:var(--muted);text-align:left;max-width:280px;margin-left:auto;margin-right:auto">
      <strong>Connect your own:</strong><br>
      1. Run: <code style="font-size:0.7rem">cd relay && ./start.sh</code><br>
      2. Tap ⚙ → paste the wss:// URL<br>
    </p>
  </div>`;
}
function tryDemo() {
  localStorage.setItem('herdr_relay_url', 'wss://herdr-remote-demo.yyrzrh5wfg.workers.dev');
  localStorage.removeItem('herdr_relay_token');
  document.getElementById('relayUrl').value = 'wss://herdr-remote-demo.yyrzrh5wfg.workers.dev';
  connect();
}

const ANSI_COLORS = ['#000000','#cd3131','#0dbc79','#e5e510','#2472c8','#bc3fbc','#11a8cd','#e5e5e5','#666666','#f14c4c','#23d18b','#f5f543','#3b8eea','#d670d6','#29b8db','#ffffff'];

function ansiFragment(input) {
  const fragment=document.createDocumentFragment();
  let state={fg:null,bg:null,inverse:false,hidden:false,fontWeight:'',opacity:'',fontStyle:'',textDecoration:'',textDecorationStyle:''};
  let cursor=0;
  const control=/\x1b\]([^\x07\x1b]*)(?:\x07|\x1b\\)|\x1b\[([0-?]*)([ -/]*[@-~])/g;
  const append=text=>{
    if(!text)return;
    const span=document.createElement('span');
    span.textContent=text;
    if(state.hidden)span.style.visibility='hidden';
    const defaultFg='#ccc';
    const defaultBg='#000';
    const fg=state.inverse?(state.bg||defaultBg):state.fg;
    const bg=state.inverse?(state.fg||defaultFg):state.bg;
    if(fg)span.style.color=fg;
    if(bg)span.style.backgroundColor=bg;
    if(state.fontWeight)span.style.fontWeight=state.fontWeight;
    if(state.opacity)span.style.opacity=state.opacity;
    if(state.fontStyle)span.style.fontStyle=state.fontStyle;
    if(state.textDecoration)span.style.textDecoration=state.textDecoration;
    if(state.textDecorationStyle)span.style.textDecorationStyle=state.textDecorationStyle;
    fragment.appendChild(span);
  };
  for(const match of input.matchAll(control)){
    append(input.slice(cursor,match.index));
    cursor=match.index+match[0].length;
    if(match[3]!=='m')continue;
    const fields=(match[2]||'0').split(';');
    const params=fields.map(value=>Number((value.split(':',1)[0])||0));
    for(let i=0;i<params.length;i++){
      const code=params[i];
      if(code===0){state={fg:null,bg:null,inverse:false,hidden:false,fontWeight:'',opacity:'',fontStyle:'',textDecoration:'',textDecorationStyle:''};continue;}
      if(code===1){state.fontWeight='700';continue;}
      if(code===2){state.opacity='0.7';continue;}
      if(code===3){state.fontStyle='italic';continue;}
      if(code===4){state.textDecoration='underline';continue;}
      if(code===7){state.inverse=true;continue;}
      if(code===8){state.hidden=true;continue;}
      if(code===22){state.fontWeight='';state.opacity='';continue;}
      if(code===23){state.fontStyle='';continue;}
      if(typeof fields[i]==='string'&&fields[i].startsWith('4:')){
        const underlineStyle=fields[i].split(':')[1];
        state.textDecoration='underline';
        state.textDecorationStyle=underlineStyle==='2'?'double':underlineStyle==='3'?'wavy':underlineStyle==='4'?'dotted':underlineStyle==='5'?'dashed':'solid';
        continue;
      }
      if(code===24){state.textDecoration='';state.textDecorationStyle='';continue;}
      if(code===27){state.inverse=false;continue;}
      if(code===28){state.hidden=false;continue;}
      if(code===39){state.fg=null;continue;}
      if(code===49){state.bg=null;continue;}
      if(code>=30&&code<=37){state.fg=ANSI_COLORS[code-30];continue;}
      if(code>=90&&code<=97){state.fg=ANSI_COLORS[code-82];continue;}
      if(code>=40&&code<=47){state.bg=ANSI_COLORS[code-40];continue;}
      if(code>=100&&code<=107){state.bg=ANSI_COLORS[code-92];continue;}
      if((code===38||code===48)&&params[i+1]===5&&Number.isInteger(params[i+2])){
        const n=params[i+2];
        const color=n<16?ANSI_COLORS[n]:n<232?`rgb(${[0,1,2].map(axis=>{const v=Math.floor((n-16)/Math.pow(6,2-axis))%6;return v?55+v*40:0}).join(',')})`:`rgb(${8+(n-232)*10},${8+(n-232)*10},${8+(n-232)*10})`;
        state[code===38?'fg':'bg']=color;i+=2;continue;
      }
      if((code===38||code===48)&&params[i+1]===2&&params.slice(i+2,i+5).every(Number.isFinite)){
        state[code===38?'fg':'bg']=`rgb(${params[i+2]},${params[i+3]},${params[i+4]})`;i+=4;
      }
    }
  }
  append(input.slice(cursor));
  return fragment;
}

