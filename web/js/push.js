// --- Web Push ---
let pushSubscription = null;

async function initPush() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    document.getElementById('pushStatus').textContent = 'Push not supported in this browser';
    document.getElementById('pushToggle').style.display = 'none';
    return;
  }
  try {
    const reg = await navigator.serviceWorker.register('/sw.js');
    pushSubscription = await reg.pushManager.getSubscription();
    updatePushUI();
  } catch (e) {
    document.getElementById('pushStatus').textContent = 'Service worker error: ' + e.message;
  }
}

function updatePushUI() {
  const btn = document.getElementById('pushToggle');
  const status = document.getElementById('pushStatus');
  if (pushSubscription) {
    btn.textContent = 'Disable Push';
    btn.style.background = 'var(--red)';
    status.innerHTML = '<span style="color:var(--green)">● Enabled</span>';
  } else {
    btn.textContent = 'Enable Push';
    btn.style.background = 'var(--green)';
    status.innerHTML = '<span style="color:var(--muted)">○ Disabled</span>';
  }
}

async function togglePush() {
  if (pushSubscription) {
    // Unsubscribe
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({type: 'push_unsubscribe', subscription: pushSubscription.toJSON()}));
    }
    await pushSubscription.unsubscribe();
    pushSubscription = null;
    updatePushUI();
  } else {
    // Subscribe
    try {
      const relayUrl = localStorage.getItem('herdr_relay_url') || '';
      const httpUrl = relayUrl.replace('wss://', 'https://').replace('ws://', 'http://');
      const resp = await fetch(httpUrl + '/api/vapid-public-key');
      const {publicKey} = await resp.json();
      if (!publicKey) {
        document.getElementById('pushStatus').textContent = 'VAPID key not configured on relay';
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      pushSubscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey)
      });
      if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify({type: 'push_subscribe', subscription: pushSubscription.toJSON()}));
      }
      updatePushUI();
    } catch (e) {
      document.getElementById('pushStatus').textContent = 'Error: ' + e.message;
    }
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

initPush();
