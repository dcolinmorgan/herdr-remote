// Attach files to a reply: the relay writes each one to the agent's machine and answers with the
// path, which goes into the reply box for the reader to send like any other text.
//
// One file at a time, one chunk at a time: the relay takes one upload per connection, and waiting
// for each ack is what keeps a phone on a slow link from filling the relay's buffers. 256 KiB of
// raw bytes is ~342 KiB as base64, which keeps every message under the relay's 1 MiB frame limit.
const UPLOAD_CHUNK_BYTES = 256 * 1024;
const UPLOAD_MAX_FILES = 5;
const UPLOAD_B64_PIECE = 32 * 1024;  // String.fromCharCode.apply overflows the stack on a whole chunk
const UPLOAD_IDLE_PLACEHOLDER = document.getElementById('termInput').placeholder;

// Set from every `agents` snapshot. 0 means the relay does not take uploads -- an older relay and
// demo-worker never send the field -- and the button stays hidden.
let uploadMaxBytes = 0;
let uploadQueue = [];        // [{file, pane}] still to go
let uploadCurrent = null;    // {id, file, pane, offset, seq, awaiting}
let uploadBatch = {done: 0, total: 0};  // what the placeholder counts: "Uploading 1 of 2…"

function updateAttachButton() {
  document.getElementById('attachBtn').hidden = !(uploadMaxBytes > 0);
}

function pickFiles() {
  document.getElementById('attachInput').click();
}

// Returns how many files it queued, so a paste can tell whether it took the clipboard.
function queueUploads(files) {
  const list = Array.from(files || []).slice(0, UPLOAD_MAX_FILES);
  if (!list.length || !(uploadMaxBytes > 0) || !activePane) return 0;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    showSendError('Not connected. Your text is still here.');
    return 0;
  }
  let queued = 0;
  for (const file of list) {
    if (file.size > uploadMaxBytes) {
      showSendError('Too large: ' + file.name + ' is over ' + Math.round(uploadMaxBytes / 1048576) + ' MB.');
      continue;
    }
    uploadQueue.push({file, pane: activePane});
    uploadBatch.total++;
    queued++;
  }
  if (queued && !uploadCurrent) startNextUpload();
  renderUploadBusy();
  return queued;
}

function startNextUpload() {
  const next = uploadQueue.shift();
  if (!next) { uploadCurrent = null; uploadBatch = {done: 0, total: 0}; renderUploadBusy(); return; }
  uploadBatch.done++;
  uploadCurrent = {id: newUploadId(), file: next.file, pane: next.pane, offset: 0, seq: 0, awaiting: 0};
  renderUploadBusy();
  ws.send(JSON.stringify({type: 'upload_start', pane_id: next.pane, upload_id: uploadCurrent.id,
    name: next.file.name, size: next.file.size}));
}

function newUploadId() {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}

async function sendNextChunk(u) {
  const end = Math.min(u.offset + UPLOAD_CHUNK_BYTES, u.file.size);
  const buf = await u.file.slice(u.offset, end).arrayBuffer();
  // The read is async: a pane switch or a dropped socket may have ended this upload meanwhile.
  if (uploadCurrent !== u || !ws || ws.readyState !== WebSocket.OPEN) return;
  u.offset = end;
  u.awaiting = end;
  ws.send(JSON.stringify({type: 'upload_chunk', upload_id: u.id, seq: u.seq++, data: uploadBase64(buf)}));
}

function uploadBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i += UPLOAD_B64_PIECE) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + UPLOAD_B64_PIECE));
  }
  return btoa(bin);
}

// True for every upload reply, including the ones it drops: a reply for an upload that is no
// longer in flight is a late answer to a cancel, and must neither insert a path nor show an error.
function handleUploadMessage(msg) {
  if (msg.type !== 'upload_ack' && msg.type !== 'upload_result') return false;
  const u = uploadCurrent;
  if (!u || msg.upload_id !== u.id) return true;
  if (msg.type === 'upload_ack') {
    if (u.awaiting === null || msg.received !== u.awaiting) return true;
    u.awaiting = null;
    if (u.offset >= u.file.size) ws.send(JSON.stringify({type: 'upload_end', upload_id: u.id}));
    else sendNextChunk(u);
    return true;
  }
  uploadCurrent = null;
  if (msg.ok && u.pane === activePane) insertAtCaret(msg.path);
  else if (!msg.ok) showSendError('Upload failed: ' + (msg.error || 'unknown error') + '. Your text is still in the box.');
  startNextUpload();
  return true;
}

// Called with no reason on a real pane switch -- the path belongs to that pane's reply and the box
// serves every pane -- and with one when the socket goes, so the reader learns the file did not land.
function cancelUploads(reason) {
  const had = uploadCurrent || uploadQueue.length;
  if (uploadCurrent && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({type: 'upload_cancel', upload_id: uploadCurrent.id}));
  }
  uploadCurrent = null;
  uploadQueue = [];
  uploadBatch = {done: 0, total: 0};
  renderUploadBusy();
  if (reason && had) showSendError(reason);
}

// The reader can keep typing while a file goes up, so the box says it is busy without being locked.
function renderUploadBusy() {
  const btn = document.getElementById('attachBtn');
  const input = document.getElementById('termInput');
  if (uploadCurrent) {
    btn.setAttribute('aria-busy', 'true');
    input.placeholder = 'Uploading ' + uploadBatch.done + ' of ' + uploadBatch.total + '…';
  } else {
    btn.removeAttribute('aria-busy');
    input.placeholder = UPLOAD_IDLE_PLACEHOLDER;
  }
}

// A space on each side only where the text beside it is not already one, and quotes around a path
// holding whitespace, so a shell or an agent reads it as one argument.
function insertAtCaret(text) {
  const input = document.getElementById('termInput');
  const word = /\s/.test(text) ? '"' + text + '"' : text;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  const before = input.value.slice(0, start);
  const after = input.value.slice(end);
  const lead = before && !/\s$/.test(before) ? ' ' : '';
  const trail = /^\s/.test(after) ? '' : ' ';
  input.value = before + lead + word + trail + after;
  const caret = (before + lead + word + trail).length;
  input.setSelectionRange(caret, caret);
}

// A paste of plain text is left to the browser; only one that actually queued files is taken.
document.getElementById('termInput').addEventListener('paste', e => {
  const files = e.clipboardData && e.clipboardData.files;
  if (!files || !files.length) return;
  if (queueUploads(files)) e.preventDefault();
});
