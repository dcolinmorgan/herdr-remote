// --- Nav Tray (collie-style) ---
let keyQueue = [], armedMod = null, ctrlConfirm = null;
const CTRL_PRESETS = [
  {label:'Ctrl C', keys:['ctrl+c']},
  {label:'Ctrl D', keys:['ctrl+d'], danger:true},
  {label:'Ctrl U', keys:['ctrl+u']},
  {label:'Ctrl R', keys:['ctrl+r']},
  {label:'Ctrl L', keys:['ctrl+l']},
  {label:'Ctrl Z', keys:['ctrl+z'], danger:true},
  // herdr's key validator refuses Home/End in every spelling; the relay turns these two into the
  // CSI bytes a terminal sends (ESC[1;5H / ESC[1;5F) and delivers them as text instead.
  {label:'Ctrl Home', keys:['ctrl+Home']},
  {label:'Ctrl End', keys:['ctrl+End']},
];
const DANGER_KEYS = new Set(['ctrl+c','ctrl+d','ctrl+z']);

function fireKey(k) {
  if(window.cue) cue('tick');
  const composed = armedMod ? `${armedMod}+${k}` : k;
  if (keyQueue.length > 0 || armedMod) {
    keyQueue.push(composed);
    armedMod = null;
    if(window.cue) cue('droplet');
    renderKeyQueue();
    renderMods();
  } else {
    sendKeys([composed]);
  }
}

function armMod(m) {
  armedMod = armedMod === m ? null : m;
  if(window.cue) cue('toggle');
  renderMods();
}

function renderMods() {
  const s = document.getElementById('modShift');
  const c = document.getElementById('modCtrl');
  if (s) s.classList.toggle('armed', armedMod === 'shift');
  if (c) c.classList.toggle('armed', armedMod === 'ctrl');
}

function renderKeyQueue() {
  const strip = document.getElementById('keyQueueStrip');
  if (!keyQueue.length && !armedMod) { strip.style.display = 'none'; return; }
  strip.style.display = 'flex';
  let html = keyQueue.map((k,i) => {
    const isDanger = DANGER_KEYS.has(k.toLowerCase());
    const label = k.includes('+') ? k.replace('ctrl+','Ctrl ').replace('shift+','\u21e7 ') : k;
    return `<span class="queue-chip${isDanger?' danger':''}" onclick="removeQueueKey(${i})">${label} \u00d7</span>`;
  }).join('');
  if (armedMod) html += `<span style="color:var(--muted);font-size:12px;padding:4px">${armedMod==='shift'?'\u21e7':'Ctrl'} + \u2026</span>`;
  html += `<span style="margin-left:auto;display:flex;gap:4px">`;
  html += `<button onclick="sendQueuedKeys()" style="padding:6px 12px;border-radius:6px;border:none;background:${keyQueue.some(k=>DANGER_KEYS.has(k.toLowerCase()))?'var(--red)':'var(--blue)'};color:var(--on-accent);font-size:12px;font-weight:600;cursor:pointer">Send</button>`;
  html += `<button onclick="clearKeyQueue()" style="padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--muted);font-size:12px;cursor:pointer">✕</button>`;
  html += `</span>`;
  strip.innerHTML = html;
}

function removeQueueKey(i) { keyQueue.splice(i,1); renderKeyQueue(); }
function clearKeyQueue() { keyQueue=[]; armedMod=null; renderKeyQueue(); renderMods(); }
function sendQueuedKeys() {
  if (!keyQueue.length) return;
  if(window.cue) cue('success');
  sendKeys(keyQueue);
  keyQueue=[]; armedMod=null; renderKeyQueue(); renderMods();
}

// The two docks are mutually exclusive, so both buttons are restated on every change -- opening one
// has to un-light the other, and that is easy to forget when only the opened one is touched.
function showDock(which) {
  const keys = which === 'keys';
  document.getElementById('termKeys').style.display = keys ? '' : 'none';
  document.getElementById('quickDock').style.display = which === 'quick' ? '' : 'none';
  setPressed('keysDockBtn', keys);
  setPressed('quickDockBtn', which === 'quick');
  if(window.cue) cue(which ? 'page' : 'tick');
}

function toggleKeysDock() {
  showDock(document.getElementById('termKeys').style.display === 'none' ? 'keys' : null);
}

function toggleQuickDock() {
  showDock(document.getElementById('quickDock').style.display === 'none' ? 'quick' : null);
}

function quickSend(text) {
  if (!ws || !activePane) return;
  if(window.cue) cue('success');
  ws.send(JSON.stringify({type:'send_text', pane_id: activePane, text: text}));
  ws.send(JSON.stringify({type:'send_keys', pane_id: activePane, keys:['Enter']}));
  showDock(null);
  setTimeout(refreshPane, 500);
}

function switchKeyTab(tab) {
  setPressed('tabKeys', tab==='keys');
  setPressed('tabDigits', tab==='digits');
  // The disclosure shares its line with the switch now, so it has to leave with the pad it opens.
  document.getElementById('presetsBtn').style.display = tab==='keys' ? '' : 'none';
  document.getElementById('keysPad').style.display = tab==='keys' ? '' : 'none';
  const dp = document.getElementById('digitsPad');
  dp.style.display = tab==='digits' ? 'grid' : 'none';
  if (tab==='digits' && !dp.innerHTML) {
    dp.innerHTML = [1,2,3,4,5,6,7,8,9].map(d => `<button class="digit-key" onclick="fireKey('${d}')">${d}</button>`).join('');
  }
}

function toggleCtrlPresets() {
  const el = document.getElementById('ctrlPresets');
  const chevron = document.getElementById('ctrlChevron');
  const show = el.style.display === 'none';
  el.style.display = show ? 'grid' : 'none';
  setPressed('presetsBtn', show);
  chevron.textContent = show ? '\u25be' : '\u25b8';
  if (show && !el.innerHTML) {
    el.innerHTML = CTRL_PRESETS.map(p => {
      const cls = p.danger ? 'ctrl-key danger' : 'ctrl-key';
      return `<button class="${cls}" onclick="pressCtrl('${p.label}')">${p.label}</button>`;
    }).join('');
  }
}

function pressCtrl(label) {
  const preset = CTRL_PRESETS.find(p => p.label === label);
  if (!preset) return;
  // If composing (queue has items), just stage it
  if (keyQueue.length > 0) { keyQueue.push(...preset.keys); renderKeyQueue(); return; }
  // Two-tap confirm for danger keys
  if (preset.danger && ctrlConfirm !== label) {
    ctrlConfirm = label;
    if(window.cue) cue('error');
    const el = document.getElementById('ctrlPresets');
    el.innerHTML = CTRL_PRESETS.map(p => {
      const cls = p.danger ? (p.label===label?'ctrl-key confirm':'ctrl-key danger') : 'ctrl-key';
      const txt = p.label===label ? 'Confirm?' : p.label;
      return `<button class="${cls}" onclick="pressCtrl('${p.label}')">${txt}</button>`;
    }).join('');
    setTimeout(() => { ctrlConfirm=null; toggleCtrlPresets(); toggleCtrlPresets(); }, 3000);
    return;
  }
  ctrlConfirm = null;
  sendKeys(preset.keys);
}

function toggleArrows(){}
function hideArrows(){}
function respond(t){if(!ws||!activePane)return;if(window.cue)cue('success');ws.send(JSON.stringify({type:'respond',pane_id:activePane,text:t}));document.getElementById('quickActions').innerHTML='';setTimeout(refreshPane,500);}
let imeComposing = false, imeEndedAt = 0;
{
  const ti = document.getElementById('termInput');
  ti.addEventListener('compositionstart',()=>{imeComposing=true;});
  ti.addEventListener('compositionend',()=>{imeComposing=false;imeEndedAt=Date.now();});
  ti.addEventListener('keydown',e=>{
    if(e.key!=='Enter')return;
    // Enter belongs to the IME while composing - intercepting it drops the preedit
    if(imeComposing||e.isComposing||e.keyCode===229)return;
    // Some Chinese IMEs emit a stray Enter right after compositionend
    if(Date.now()-imeEndedAt<80)return;
    e.preventDefault();sendText();
  });
}
// `scroll` says nothing about which axis moved, and a long line is exactly when the reader moves
// the other one. `scrollTop === 0` is true for the whole of a horizontal drag -- and permanently
// true whenever the output is shorter than the box -- so this fired loadMore on every scroll event
// sideways: measured, one wheel right took the read from 200 lines to 600 and the next to 1000,
// each answer a different content that replaced the mirror under the reader's hands. Only a
// vertical move that ARRIVES at the top is a request for more.
let mirrorScrollTop = 0;
document.getElementById('termContent').addEventListener('scroll', function() {
  const el = this;
  userScrolledUp = (el.scrollHeight - el.scrollTop - el.clientHeight) > 50;
  const moved = el.scrollTop !== mirrorScrollTop;
  mirrorScrollTop = el.scrollTop;
  if (moved && el.scrollTop === 0 && el.scrollHeight > el.clientHeight) loadMore();
});
document.getElementById('historyContent').addEventListener('scroll', function() {
  if (this.scrollTop <= 4) loadOlderHistory();
});
window.addEventListener('resize', positionHistoryPanel);

if (savedUrl) setTimeout(connect, 100); else showSetup();

