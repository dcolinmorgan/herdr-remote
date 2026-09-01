// herdr-remote service worker — Web Push notifications
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', (event) => {
  let data = { title: '🐑 herdr', body: 'Agent needs attention', url: '/' };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (e) {}
  // Clear notification (sent when agent unblocks)
  if (data.type === 'clear') {
    event.waitUntil(
      self.registration.getNotifications({ tag: data.tag || 'herdr-blocked' }).then((notes) => {
        notes.forEach((n) => n.close());
      })
    );
    return;
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      // PNG, not the svg: svg notification icons are not reliably supported, and a notification
      // that silently drops its icon is the one place there is no fallback to look at.
      icon: '/icon-192.png',
      badge: '/favicon-32.png',
      tag: 'herdr-blocked',
      renotify: true,
      data: { url: data.url },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes(self.location.origin)) {
          client.focus();
          client.postMessage({ type: 'navigate', url });
          return;
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
