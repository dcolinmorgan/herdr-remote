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

// The relay only ever hears about a subscription when somebody taps the toggle, so its copy is a
// snapshot of that one moment -- while the browser hands out a new endpoint of its own accord (a
// reinstall, a long idle, a subscription it decided to retire). Nothing reports that: the toggle
// reads Enabled because it asks the BROWSER, and the relay goes on pushing to the old endpoint,
// which APNs answers 201 for whether or not anything is behind it. Re-sending the live
// subscription on every connect closes that: the relay dedupes by value, so an unchanged one
// costs a few hundred bytes and a rotated one is registered before the next block -- and the
// relay's "Push subscription added" line then says, in the log, that a rotation is what happened.
async function resyncPushSubscription() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && await reg.pushManager.getSubscription();
    if (!sub) return;
    pushSubscription = sub;
    updatePushUI();
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({type: 'push_subscribe', subscription: sub.toJSON()}));
    }
  } catch (e) {}
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
