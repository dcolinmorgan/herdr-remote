"""A deterministic OpenAI-compatible provider; real harnesses still run their tools."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ModelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, ModelHandler)
        self.requests = []


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, body, content_type='application/json'):
        payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.respond({'object': 'list', 'data': [{'id': 'iris-fixture', 'object': 'model', 'owned_by': 'test'}]})

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(request)
        time.sleep(5)  # Long enough to observe typing refresh, without token costs.
        tools = [tool.get('function', {}) for tool in request.get('tools', [])]
        read_tool = next((tool for tool in tools if tool.get('name') in ('read', 'read_file', 'file_read')), None)
        tool_result = any(message.get('role') == 'tool' and 'IRIS_TOOL_OK' in str(message.get('content')) for message in request.get('messages', []))
        message = {'role': 'assistant', 'content': 'IRIS_TEST_OK' if tool_result else 'IRIS_TEXT_OK'}
        finish = 'stop'
        if read_tool and not tool_result:
            properties = read_tool.get('parameters', {}).get('properties', {})
            path_key = next((key for key in ('filePath', 'path', 'file_path') if key in properties), None)
            if path_key:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'iris_read_marker', 'type': 'function',
                    'function': {'name': read_tool['name'], 'arguments': json.dumps({path_key: '/tmp/test-workspace/marker.txt'})}}]}
                finish = 'tool_calls'
        base = {'id': 'iris-fixture-completion', 'object': 'chat.completion', 'created': int(time.time()), 'model': 'iris-fixture'}
        if request.get('stream'):
            delta = dict(message)
            if 'tool_calls' in delta:
                delta['tool_calls'][0]['index'] = 0
            chunks = [{**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                      {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                       'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}]
            self.respond(''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n', 'text/event-stream')
        else:
            self.respond({**base, 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                          'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}})
