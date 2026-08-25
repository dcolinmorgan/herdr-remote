// --- Command Palette ---
const COMMANDS = {
  claude: [
    {cmd: '/compact', desc: 'Summarize context to free tokens', common: true},
    {cmd: '/clear', desc: 'Start fresh conversation', common: true, danger: true},
    {cmd: '/model', desc: 'Switch model', common: true},
    {cmd: '/status', desc: 'Show version and connectivity', common: true},
    {cmd: '/context', desc: 'Visualize context-window usage', common: true},
    {cmd: '/review', desc: 'Review a pull request', common: true},
    {cmd: '/diff', desc: 'Show uncommitted changes', common: false},
    {cmd: '/resume', desc: 'Resume previous conversation', common: false},
    {cmd: '/help', desc: 'Show all commands', common: false},
  ],
  codex: [
    {cmd: '/compact', desc: 'Summarize history to free context', common: true},
    {cmd: '/clear', desc: 'Reset and start new chat', common: true, danger: true},
    {cmd: '/diff', desc: 'Show git diff of working tree', common: true},
    {cmd: '/model', desc: 'Switch model', common: true},
    {cmd: '/status', desc: 'Show model and token usage', common: true},
    {cmd: '/review', desc: 'Code review working tree', common: true},
    {cmd: '/mention', desc: 'Attach files to context', common: false},
    {cmd: '/plan', desc: 'Enter plan mode', common: false},
  ],
  pi: [
    {cmd: '/compact', desc: 'Compact context', common: true},
    {cmd: '/new', desc: 'Start new session', common: true, danger: true},
    {cmd: '/model', desc: 'Switch model', common: true},
    {cmd: '/session', desc: 'Show session info', common: true},
    {cmd: '/tree', desc: 'Jump to earlier point', common: true},
    {cmd: '/share', desc: 'Share session as gist', common: true},
    {cmd: '/copy', desc: 'Copy last response', common: false},
    {cmd: '/reload', desc: 'Reload extensions and skills', common: false},
  ],
  opencode: [
    {cmd: '/compact', desc: 'Compact current session', common: true},
    {cmd: '/new', desc: 'Start new session', common: true, danger: true},
    {cmd: '/models', desc: 'List and switch models', common: true},
    {cmd: '/undo', desc: 'Undo last turn and revert', common: true, danger: true},
    {cmd: '/share', desc: 'Share session', common: true},
    {cmd: '/diff', desc: 'Show working changes', common: false},
    {cmd: '/export', desc: 'Export to Markdown', common: false},
  ],
};

function getAgentCommands() {
  const a = agents.find(x => x.pane_id === activePane);
  if (!a) return [];
  const key = (a.agent || '').toLowerCase();
  if (COMMANDS[key]) return COMMANDS[key];
  if (key.startsWith('claude')) return COMMANDS.claude;
  if (key.startsWith('codex')) return COMMANDS.codex;
  if (key.startsWith('pi') || key === 'kiro') return COMMANDS.pi;
  if (key.startsWith('opencode')) return COMMANDS.opencode;
  return COMMANDS.claude; // fallback
}

function openCommandPalette() {
  if(window.cue) cue('page');
  document.getElementById('cmdPalette').style.display = '';
  navPush('palette', hidePalette);
  document.getElementById('cmdSearch').value = '';
  filterCommands();
  document.getElementById('cmdSearch').focus();
}
function closePalette() { navClose('palette', hidePalette); }
function hidePalette() { document.getElementById('cmdPalette').style.display = 'none'; }

function filterCommands() {
  const q = (document.getElementById('cmdSearch').value || '').toLowerCase();
  const cmds = getAgentCommands();
  const filtered = q ? cmds.filter(c => c.cmd.includes(q) || c.desc.toLowerCase().includes(q)) : cmds.filter(c => c.common);
  const el = document.getElementById('cmdList');
  if (!filtered.length) { el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted);font-size:0.8rem">No commands match</div>'; return; }
  el.innerHTML = filtered.map(c => `<div role="button" tabindex="0" onclick="runCommand('${c.cmd}')" onkeydown="if(event.key==='Enter')runCommand('${c.cmd}')" style="padding:10px 12px;border-radius:8px;cursor:pointer;display:flex;align-items:center;gap:8px;margin-bottom:2px;transition:background 0.15s" onmouseover="this.style.background='var(--border)'" onmouseout="this.style.background=''">
    <span style="font-family:monospace;font-weight:600;font-size:0.85rem;${c.danger?'color:var(--red)':'color:var(--blue)'}">${c.cmd}</span>
    <span style="flex:1;font-size:0.75rem;color:var(--muted)">${c.desc}</span>
  </div>`).join('');
}

function runCommand(cmd) {
  closePalette();
  if (!ws || !activePane) return;
  ws.send(JSON.stringify({type:'send_text', pane_id: activePane, text: cmd}));
  ws.send(JSON.stringify({type:'send_keys', pane_id: activePane, keys:['Enter']}));
  setTimeout(refreshPane, 500);
}

