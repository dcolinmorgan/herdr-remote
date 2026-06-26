export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Handle HTTP POST (plugin push simulation)
    if (request.method === 'POST') {
      return new Response('ok\n', { headers: { 'Access-Control-Allow-Origin': '*' } });
    }

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' } });
    }

    // WebSocket upgrade
    const upgrade = request.headers.get('Upgrade');
    if (upgrade !== 'websocket') {
      return new Response('herdr-remote demo relay. Connect via WebSocket.', {
        headers: { 'Access-Control-Allow-Origin': '*' }
      });
    }

    const [client, server] = Object.values(new WebSocketPair());
    server.accept();

    const agents = [
      { pane_id: 'demo:1', agent: 'claude', status: 'working', project: 'phoenix-api', cwd: '/dev/phoenix-api', host: 'local' },
      { pane_id: 'demo:2', agent: 'codex', status: 'idle', project: 'nova-ingest', cwd: '/dev/nova-ingest', host: 'local' },
      { pane_id: 'demo:3', agent: 'kiro', status: 'blocked', project: 'orbit-ui', cwd: '/dev/orbit-ui', host: 'local' },
      { pane_id: 'demo:4', agent: 'grok', status: 'working', project: 'atlas-core', cwd: '/dev/atlas-core', host: 'remote-1' },
      { pane_id: 'demo:5', agent: 'copilot', status: 'idle', project: 'delta-sync', cwd: '/dev/delta-sync', host: 'local' },
      { pane_id: 'demo:6', agent: 'claude', status: 'working', project: 'nebula-ml', cwd: '/dev/nebula-ml', host: 'remote-2' },
    ];

    const blockedPrompt = `Do you want to allow this tool call?\n\nTool: write_file\nPath: src/components/Graph.tsx\n\n> yes, single permission\n> trust, always allow\n> no (tab to edit)`;

    // Send initial state
    server.send(JSON.stringify({ type: 'agents', agents }));

    // Send blocked event for the blocked agent
    server.send(JSON.stringify({
      type: 'blocked', pane_id: 'demo:3', agent: 'claude', project: 'graffold-frontend',
      prompt: blockedPrompt, host: 'local',
      options: ['yes, single permission', 'trust, always allow', 'no (tab to edit)']
    }));

    // Periodic updates
    let interval = setInterval(() => {
      // Randomly change one agent status
      const idx = Math.floor(Math.random() * agents.length);
      const statuses = ['working', 'idle', 'blocked'];
      agents[idx].status = statuses[Math.floor(Math.random() * statuses.length)];
      try {
        server.send(JSON.stringify({ type: 'agents', agents }));
        if (agents[idx].status === 'blocked') {
          server.send(JSON.stringify({
            type: 'blocked', pane_id: agents[idx].pane_id, agent: agents[idx].agent,
            project: agents[idx].project, prompt: blockedPrompt, host: agents[idx].host,
            options: ['yes, single permission', 'trust, always allow', 'no (tab to edit)']
          }));
        }
      } catch { clearInterval(interval); }
    }, 5000);

    server.addEventListener('message', (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'read_pane') {
          server.send(JSON.stringify({
            type: 'pane_content', pane_id: msg.pane_id,
            content: `$ herdr agent session\n\n[demo mode — read-only preview]\n\nAgent: ${msg.pane_id.split(':')[1]}\nProject: ${agents.find(a => a.pane_id === msg.pane_id)?.project || 'unknown'}\n\n✓ Compiled successfully\n→ Running tests...\n\n  PASS src/index.test.ts\n  PASS src/utils.test.ts\n\nAll tests passed.`
          }));
        } else if (msg.type === 'respond') {
          const a = agents.find(x => x.pane_id === msg.pane_id);
          if (a) a.status = 'working';
          server.send(JSON.stringify({ type: 'agents', agents }));
        }
      } catch {}
    });

    server.addEventListener('close', () => clearInterval(interval));

    return new Response(null, { status: 101, webSocket: client });
  }
};
