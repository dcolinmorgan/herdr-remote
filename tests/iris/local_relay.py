"""Minimal in-memory Nostr relay for encrypted, network-isolated integration tests.

This is a test fixture, not a public relay: it deliberately does not verify signatures.
The real Iris clients perform encryption, decryption, and peer authentication.
"""
import base64
import hashlib
import json
import socketserver
import struct
import threading


class Relay(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Connection)
        self.events = {}
        self.clients = set()
        self.guard = threading.Lock()


def matches(event, filters):
    for rule in filters:
        valid = True
        for key, wanted in rule.items():
            if key in ('limit', 'search'):
                continue
            if key == 'kinds':
                valid &= event.get('kind') in wanted
            elif key in ('authors', 'ids'):
                actual = event.get('pubkey' if key == 'authors' else 'id', '')
                valid &= any(actual.startswith(prefix) for prefix in wanted)
            elif key == 'since':
                valid &= event.get('created_at', 0) >= wanted
            elif key == 'until':
                valid &= event.get('created_at', 0) <= wanted
            elif key.startswith('#'):
                valid &= any(tag[0] == key[1:] and len(tag) > 1 and tag[1] in wanted for tag in event.get('tags', []))
        if valid:
            return True
    return False


class Connection(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.send_guard = threading.Lock()
        self.subscriptions = {}

    def send_frame(self, payload, opcode=1):
        length = len(payload)
        header = bytes([0x80 | opcode, length]) if length < 126 else (
            bytes([0x80 | opcode, 126]) + struct.pack('!H', length) if length < 65536
            else bytes([0x80 | opcode, 127]) + struct.pack('!Q', length))
        with self.send_guard:
            self.wfile.write(header + payload)
            self.wfile.flush()

    def send(self, value):
        self.send_frame(json.dumps(value).encode())

    def handle(self):
        request = self.rfile.readline()
        if not request:
            return
        headers = {}
        while line := self.rfile.readline():
            if line == b'\r\n':
                break
            key, _, value = line.decode().partition(':')
            headers[key.lower()] = value.strip()
        key = headers.get('sec-websocket-key')
        if not key:
            self.wfile.write(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
            return
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        self.wfile.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
        self.wfile.flush()
        with self.server.guard:
            self.server.clients.add(self)
        try:
            while first := self.rfile.read(2):
                if len(first) != 2:
                    break
                opcode, length = first[0] & 15, first[1] & 127
                if length == 126:
                    length = struct.unpack('!H', self.rfile.read(2))[0]
                elif length == 127:
                    length = struct.unpack('!Q', self.rfile.read(8))[0]
                if length > 2 * 1024 * 1024:
                    break
                mask = self.rfile.read(4) if first[1] & 128 else b''
                body = self.rfile.read(length)
                if mask:
                    body = bytes(byte ^ mask[i % 4] for i, byte in enumerate(body))
                if opcode == 8:
                    break
                if opcode == 9:
                    self.send_frame(body, 10)
                if opcode != 1:
                    continue
                packet = json.loads(body)
                if packet[0] == 'REQ':
                    subscription, filters = packet[1], packet[2:]
                    with self.server.guard:
                        self.subscriptions[subscription] = filters
                        events = list(self.server.events.values())
                    for event in events:
                        if matches(event, filters):
                            self.send(['EVENT', subscription, event])
                    self.send(['EOSE', subscription])
                elif packet[0] == 'CLOSE':
                    self.subscriptions.pop(packet[1], None)
                elif packet[0] == 'EVENT':
                    event = packet[1]
                    with self.server.guard:
                        self.server.events[event['id']] = event
                        clients = list(self.server.clients)
                    self.send(['OK', event['id'], True, ''])
                    for client in clients:
                        for subscription, filters in list(client.subscriptions.items()):
                            if matches(event, filters):
                                try:
                                    client.send(['EVENT', subscription, event])
                                except OSError:
                                    pass
        except (OSError, ValueError, struct.error):
            pass
        finally:
            with self.server.guard:
                self.server.clients.discard(self)
