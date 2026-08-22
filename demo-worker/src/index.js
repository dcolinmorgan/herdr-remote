export default {
  async fetch(request, env) {
    if (request.method === 'POST') {
      return new Response('ok\n', { headers: { 'Access-Control-Allow-Origin': '*' } });
    }
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' } });
    }

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

    // A canned transcript so the demo's history panel shows what the real one shows. The real
    // relay reads the agent's own JSONL; there is nothing to read here, and an unanswered
    // get_history leaves the panel spinning.
    const demoTurns = [
      { uuid: 'd1', role: 'user', text: 'The graph view redraws on every websocket frame. Can you make it only redraw when the data actually changed?', ts: '2026-08-21T09:02:11Z', truncated: false },
      { uuid: 'd2', role: 'assistant', text: 'Let me look at how the component subscribes first.', ts: '2026-08-21T09:02:19Z', truncated: false },
      { uuid: 'd3', role: 'tool', text: 'Grep(useEffect.*socket) → src/components/Graph.tsx:41', ts: '2026-08-21T09:02:20Z', truncated: false },
      { uuid: 'd4', role: 'tool', text: 'Read(src/components/Graph.tsx) → import { useEffect, useState } from "react"', ts: '2026-08-21T09:02:22Z', truncated: false },
      { uuid: 'd5', role: 'assistant', text: 'Found it: the effect has no dependency array, so every frame re-runs the layout pass. I will hash the series and bail out when it matches the last render.', ts: '2026-08-21T09:02:41Z', truncated: false },
      { uuid: 'd6', role: 'note', text: 'Compacted (ctrl+o to see full summary)', ts: '2026-08-21T09:07:03Z', truncated: false },
      { uuid: 'd7', role: 'user', text: 'Good. Add a test that fails on the old behaviour.', ts: '2026-08-21T09:11:50Z', truncated: false },
      { uuid: 'd8', role: 'tool', text: 'Bash(npm test -- Graph) → PASS src/components/Graph.test.tsx', ts: '2026-08-21T09:12:34Z', truncated: false },
      { uuid: 'd9', role: 'assistant', text: 'Done: the redraw is gated on a content hash, and Graph.test.tsx asserts one render per data change (it fails with 12 on the old code).', ts: '2026-08-21T09:12:58Z', truncated: false },
    ];

    const blockedPrompt = `Do you want to allow this tool call?\n\nTool: write_file\nPath: src/components/Graph.tsx\n\n> yes, single permission\n> trust, always allow\n> no (tab to edit)`;

    server.send(JSON.stringify({ type: 'agents', agents }));
    server.send(JSON.stringify({
      type: 'blocked', pane_id: 'demo:3', agent: 'kiro', project: 'orbit-ui',
      prompt: blockedPrompt, host: 'local',
      options: ['yes, single permission', 'trust, always allow', 'no (tab to edit)']
    }));

    let interval = setInterval(() => {
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
            content: `$ herdr agent session\n\n[demo mode -- read-only preview]\n\nAgent: ${msg.pane_id.split(':')[1]}\nProject: ${agents.find(a => a.pane_id === msg.pane_id)?.project || 'unknown'}\n\n  Compiled successfully\n  Running tests...\n\n  PASS src/index.test.ts\n  PASS src/utils.test.ts\n\nAll tests passed.`
          }));
        } else if (msg.type === 'get_history') {
          const turns = msg.include_tools ? demoTurns : demoTurns.filter(t => t.role !== 'tool');
          server.send(JSON.stringify({
            type: 'history', pane_id: msg.pane_id, messages: turns, total: turns.length,
            has_more: false, title: 'gate the graph redraw on changed data',
            agent: agents.find(a => a.pane_id === msg.pane_id)?.agent || 'claude',
            file_truncated: false, unavailable: null,
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
