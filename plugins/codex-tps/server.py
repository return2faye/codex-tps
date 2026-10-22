#!/usr/bin/env python3
"""Local MCP App + CLI for experimental Codex Desktop stream measurements."""
import argparse
from collections import deque
import json
from pathlib import Path
import select
import socket
import sqlite3
import sys
import threading
import time
import uuid

from probe import connect_ipc, frame, read_message

ROOT = Path(__file__).resolve().parent
HOME = Path.home() / '.codex'
UI = 'ui://codex-tps/dashboard'
VERSION = '0.1.4'
SNAPSHOT_TIMEOUT = 10


def thread_uuid(value):
    if not isinstance(value, str):
        raise ValueError('thread_id must be a UUID string')
    return str(uuid.UUID(value))


def request_thread(params, args):
    meta = params.get('_meta', {})
    if not isinstance(meta, dict):
        raise ValueError('MCP metadata must be an object')
    value = meta.get('threadId', args.get('thread_id'))
    if value is None:
        raise ValueError('Open Codex TPS inside a local Codex conversation; the host did not provide its thread context.')
    thread_id = thread_uuid(value)
    if 'thread_id' in args and thread_uuid(args['thread_id']) != thread_id:
        raise ValueError('This panel can only observe its own conversation')
    return thread_id


def threads():
    files = list(HOME.glob('state_*.sqlite'))
    if not files:
        return []
    path = max(files, key=lambda p: int(p.stem.split('_')[-1]))
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        return [dict(id=r[0], title=r[1]) for r in conn.execute('''
            SELECT id,title FROM threads WHERE archived=0
            AND (thread_source IS NULL OR thread_source!='subagent')
            ORDER BY recency_at_ms DESC LIMIT 30''')]


def splice_utf16(text, at, delete, insert):
    raw = text.encode('utf-16-le', errors='surrogatepass')
    if not isinstance(at, int) or not isinstance(delete, int) or at < 0 or delete < 0 or (at+delete)*2 > len(raw):
        raise ValueError('Invalid UTF-16 edit; cannot measure this stream safely')
    return (raw[:at*2] + insert.encode('utf-16-le', errors='surrogatepass') + raw[(at+delete)*2:]).decode('utf-16-le', errors='surrogatepass')


class Meter:
    """Keep streaming text estimates separate from batched reported usage."""
    def __init__(self, encoder=None):
        self.encoder = encoder
        self.texts = {}
        self.owner = None
        self.revision = None
        self.samples = deque()
        self.total_tokens = 0
        self.total_chars = 0
        self.text_updates = 0
        self.unknown_items = 0
        self.started = time.monotonic()
        self.last_delta = None
        self.usage_total = None
        self.usage_output_tokens = 0
        self.usage_reasoning_tokens = 0
        self.usage_updates = 0
        self.usage_resets = 0
        self.last_usage = None

    def update_usage(self, value, now, baseline=False):
        total = value.get('total') if isinstance(value, dict) else None
        output = total.get('outputTokens') if isinstance(total, dict) else None
        reasoning = total.get('reasoningOutputTokens') if isinstance(total, dict) else None
        previous = self.usage_total
        if (type(output) is not int or type(reasoning) is not int
                or not 0 <= reasoning <= output):
            if previous is not None:
                self.usage_resets += 1
            self.usage_total = None
            return
        self.usage_total = (output, reasoning)
        if previous is None:
            return
        delta = (output-previous[0], reasoning-previous[1])
        if baseline or not 0 <= delta[1] <= delta[0]:
            if delta != (0, 0):
                self.usage_resets += 1
            return
        if delta == (0, 0):
            return
        self.usage_output_tokens += delta[0]
        self.usage_reasoning_tokens += delta[1]
        self.usage_updates += 1
        self.last_usage = now

    def index_items(self, value):
        if isinstance(value, dict):
            if value.get('type') in ('agentMessage', 'plan') and isinstance(value.get('id'), str):
                self.texts[value['id']] = value.get('text', '')
            else:
                for child in value.values():
                    self.index_items(child)
        elif isinstance(value, list):
            for child in value:
                self.index_items(child)

    def count(self, text):
        return len(self.encoder.encode(text, disallowed_special=())) if self.encoder else 0

    def consume(self, msg, thread, now=None):
        now = time.monotonic() if now is None else now
        if msg.get('type') != 'broadcast' or msg.get('method') != 'thread-stream-state-changed':
            return
        p = msg.get('params', {})
        if p.get('conversationId') != thread or p.get('hostId') != 'local':
            return
        if msg.get('version') != 11:
            raise ValueError('Unsupported IPC stream version; this plugin needs an update')
        change = p['change']
        if change['type'] == 'snapshot':
            self.owner = msg['sourceClientId']
            self.revision = change['revision']
            self.texts.clear()
            self.index_items(change['conversationState'])
            self.update_usage(change['conversationState'].get('latestTokenUsageInfo'), now, baseline=True)
            # A snapshot establishes a baseline; its history never becomes output.
            self.samples.clear()
            return
        if change['type'] != 'patches' or msg.get('sourceClientId') != self.owner:
            return
        if change['revision'] == self.revision:
            return
        if change['baseRevision'] != self.revision:
            raise ValueError('IPC revisions skipped; stop and restart the monitor')
        self.revision = change['revision']
        changes = change.get('acceptedTextChanges') or []
        # Text edits describe the pre-patch state, so apply them before indexing
        # newly inserted complete items from this same patch batch.
        for item in changes:
            if item.get('type') != 'text' or item.get('target', {}).get('field') != 'text':
                continue
            key = item['key']['itemId']
            old = self.texts.get(key)
            if old is None:
                if not item.get('edits') or item['edits'][0].get('at') != 0:
                    self.unknown_items += 1
                    continue
                old = ''
            new = old
            for edit in item['edits']:
                new = splice_utf16(new, edit['at'], edit['deleteCount'], edit['insert'])
            self.texts[key] = new
            dt, dc = self.count(new)-self.count(old), len(new)-len(old)
            self.total_tokens += dt
            self.total_chars += dc
            self.text_updates += 1
            self.samples.append((now, dt, dc))
            self.last_delta = now
        for patch in change.get('patches', []):
            # The tested desktop publishes complete usage objects, not token deltas.
            if patch.get('path') == ['latestTokenUsageInfo']:
                self.update_usage(patch.get('value'), now)
            self.index_items(patch.get('value'))

    def status(self, now=None):
        now = time.monotonic() if now is None else now
        while self.samples and self.samples[0][0] < now-2:
            self.samples.popleft()
        window = max(0.5, min(2, now-self.started))
        tokens = max(0, sum(x[1] for x in self.samples))/window
        chars = max(0, sum(x[2] for x in self.samples))/window
        return dict(tps=round(tokens, 1) if self.encoder else None,
                    chars_per_second=round(chars, 1), visible_tokens=max(0, self.total_tokens) if self.encoder else None,
                    visible_chars=max(0, self.total_chars), text_updates=self.text_updates,
                    snapshot_received=self.revision is not None, skipped_items=self.unknown_items,
                    last_delta_age_s=None if self.last_delta is None else round(now-self.last_delta, 2),
                    reported_output_tokens=self.usage_output_tokens if self.usage_total is not None else None,
                    reported_reasoning_tokens=self.usage_reasoning_tokens if self.usage_total is not None else None,
                    usage_updates=self.usage_updates, usage_resets=self.usage_resets,
                    last_usage_age_s=None if self.last_usage is None else round(now-self.last_usage, 2),
                    tokenizer='o200k_base (estimate)' if self.encoder else 'unavailable; characters only')


class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker = None
        self.sock = None
        self.thread_id = None
        self.meter = Meter()
        self.state = 'stopped'
        self.error = None

    def status(self):
        with self.lock:
            return dict(state=self.state, thread_id=self.thread_id, error=self.error,
                        metric='Visible text speed, rolling 2 s; reported output usage is separate and includes tools and reasoning',
                        experimental=True, **self.meter.status())

    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.worker:
            self.worker.join(timeout=6)
            if self.worker.is_alive():
                raise RuntimeError('Previous monitor has not stopped; retry shortly')
        with self.lock:
            self.state = 'stopped'
        return self.status()

    def start(self, thread_id):
        thread_id = thread_uuid(thread_id)
        self.stop()
        self.stop_event = threading.Event()
        with self.lock:
            self.thread_id = thread_id
            self.error = None
            self.state = 'connecting'
            self.meter = Meter()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()
        return self.status()

    def run(self):
        client = 'initializing-client'
        subscribed = False
        def follow(value):
            return dict(type='broadcast', method='thread-stream-following-changed', version=1,
                        sourceClientId=client, params=dict(conversationId=self.thread_id, hostId='local', following=value))
        try:
            # Optional dependency: failure is explicit and never converted into fake TPS.
            try:
                import tiktoken
                encoder = tiktoken.get_encoding('o200k_base')
            except (ImportError, OSError, ValueError) as exc:
                encoder = None
                print('Tokenizer unavailable: '+str(exc), file=sys.stderr)
            with self.lock:
                self.meter.encoder = encoder
            if self.stop_event.is_set():
                return
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                self.sock = sock
                sock.settimeout(5)
                connect_ipc(sock, HOME)
                request_id = str(uuid.uuid4())
                with sock.makefile('rb', buffering=0) as reader:
                    sock.sendall(frame(dict(type='request', method='initialize', version=0,
                        sourceClientId=client, requestId=request_id, params=dict(clientType='codex-tps'))))
                    deadline = time.monotonic()+10
                    while not self.stop_event.is_set() and time.monotonic() < deadline:
                        msg = read_message(reader)
                        if msg.get('type') == 'response' and msg.get('requestId') == request_id:
                            if msg.get('resultType') != 'success':
                                raise ValueError('IPC initialization rejected')
                            client = msg['result']['clientId']
                            break
                    else:
                        raise TimeoutError('IPC initialization timed out')
                    sock.sendall(frame(follow(True)))
                    subscribed = True
                    with self.lock:
                        self.state = 'waiting_for_snapshot'
                    deadline = time.monotonic()+SNAPSHOT_TIMEOUT
                    # Wait with select so an idle conversation is not a timeout error.
                    while not self.stop_event.is_set():
                        if self.meter.revision is None and time.monotonic() >= deadline:
                            raise TimeoutError('IPC initialized but no snapshot arrived within 10 s. Open the selected local conversation in Codex Desktop and retry. If it is already open, restart Codex Desktop normally and retry; this restored streaming on the tested build.')
                        if not select.select([sock], [], [], 0.25)[0]:
                            continue
                        msg = read_message(reader)
                        if msg.get('type') == 'client-discovery-request':
                            sock.sendall(frame(dict(type='client-discovery-response', requestId=msg['requestId'], response=dict(canHandle=False))))
                        if msg.get('type') == 'broadcast' and msg.get('method') == 'thread-stream-following-status-requested':
                            p = msg.get('params', {})
                            if p.get('conversationId') == self.thread_id and p.get('hostId') == 'local':
                                sock.sendall(frame(follow(True)))
                        with self.lock:
                            self.meter.consume(msg, self.thread_id)
                            if self.meter.revision is not None:
                                self.state = 'subscribed'
                    if subscribed:
                        sock.sendall(frame(follow(False)))
        except Exception as exc:
            if not self.stop_event.is_set():
                with self.lock:
                    self.state = 'error'
                    self.error = str(exc)
        finally:
            self.sock = None


def result(data):
    return dict(content=[dict(type='text', text=json.dumps(data, ensure_ascii=False))], structuredContent=data)


def tool(name, description, properties=None, required=None, ui=False):
    value = dict(name=name, title=description, description=description,
                 inputSchema=dict(type='object', properties=properties or {}, additionalProperties=False),
                 annotations=dict(readOnlyHint=name not in ('codex_tps_start','codex_tps_stop'), destructiveHint=False, openWorldHint=False))
    if required:
        value['inputSchema']['required'] = required
    if ui:
        value['title'] = 'Codex TPS'
        value['_meta'] = {'ui': {'resourceUri': UI}, 'openai/ui': {'entrypoints': [{'type': 'thread'}]}}
    return value


TOOLS = [tool('codex_tps_open', 'Open the live TPS panel for this conversation; it starts observing automatically', ui=True),
         tool('codex_tps_threads', 'List recent local conversations to select for measurement'),
         tool('codex_tps_start', 'Start observing the current conversation; thread_id is only needed by clients without host context', {'thread_id': {'type':'string', 'format':'uuid'}}),
         tool('codex_tps_status', 'Read throughput for the current conversation; does not send messages to the model'),
         tool('codex_tps_stop', 'Stop observing the current conversation')]


def dispatch(method, params, monitors):
    if method == 'initialize':
        return dict(protocolVersion='2025-11-25', capabilities=dict(tools={}, resources={}), serverInfo=dict(name='codex-tps', version=VERSION))
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return dict(tools=TOOLS)
    if method == 'resources/list':
        return dict(resources=[dict(uri=UI, name='Codex TPS', mimeType='text/html;profile=mcp-app')])
    if method == 'resources/read' and params.get('uri') == UI:
        return dict(contents=[dict(uri=UI, mimeType='text/html;profile=mcp-app', text=(ROOT/'dashboard.html').read_text(),
                    _meta={'openai/ui': {'preferredDisplayMode':'fullscreen', 'availableDisplayModes':['inline','fullscreen']}, 'ui': {'csp': {'connectDomains':[], 'resourceDomains':[]}}})])
    if method == 'tools/call':
        args = params.get('arguments', {})
        try:
            if not isinstance(args, dict):
                raise ValueError('Tool arguments must be an object')
            name = params['name']
            if name == 'codex_tps_threads':
                return result(dict(threads=threads()))
            if name in ('codex_tps_open','codex_tps_status','codex_tps_start','codex_tps_stop'):
                thread_id = request_thread(params, args)
                if thread_id not in monitors:
                    monitors[thread_id] = Monitor()
                    monitors[thread_id].thread_id = thread_id
                monitor = monitors[thread_id]
                if name == 'codex_tps_start':
                    # Opening another panel for this thread must not reset its meter.
                    if monitor.status()['state'] not in ('connecting','waiting_for_snapshot','subscribed'):
                        return result(monitor.start(thread_id))
                if name == 'codex_tps_stop':
                    return result(monitor.stop())
                return result(monitor.status())
        except (ValueError, OSError, KeyError, RuntimeError, sqlite3.Error) as exc:
            return dict(content=[dict(type='text', text=str(exc))], isError=True)
    raise ValueError('Unsupported method or resource: '+method)


def serve():
    monitors = {}
    try:
        for line in sys.stdin:
            request = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError('MCP request must be an object')
                if 'id' not in request:
                    continue
                params = request.get('params', {})
                if not isinstance(params, dict):
                    raise ValueError('MCP params must be an object')
                response = dict(jsonrpc='2.0', id=request['id'], result=dispatch(request['method'], params, monitors))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                response = dict(jsonrpc='2.0', id=request.get('id') if isinstance(request, dict) else None,
                                error=dict(code=-32602, message=str(exc)))
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        for monitor in monitors.values():
            monitor.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['mcp','threads','watch'])
    parser.add_argument('--thread', help='Conversation UUID; required for watch')
    parser.add_argument('--json', action='store_true', help='Print machine-readable metrics every 500 ms')
    args = parser.parse_args()
    if args.command == 'mcp':
        serve()
    elif args.command == 'threads':
        print(json.dumps(dict(threads=threads()), ensure_ascii=False, indent=2))
    else:
        if not args.thread:
            parser.error('watch requires --thread UUID (see threads)')
        monitor = Monitor()
        try:
            monitor.start(args.thread)
            while True:
                state = monitor.status()
                if args.json:
                    print(json.dumps(state), flush=True)
                else:
                    speed = f"~{state['tps']:.1f} visible tok/s" if state['tps'] is not None else f"{state['chars_per_second']:.1f} visible chars/s (no tokenizer)"
                    output = state['reported_output_tokens']
                    usage = 'reported output: waiting' if output is None else f"reported output={output} (reasoning included={state['reported_reasoning_tokens']})"
                    if state['usage_resets']:
                        usage += ' [usage gap excluded]'
                    print(f"\r{state['state']:<22} {speed:<42} {usage} {state['error'] or ''}", end='', flush=True)
                if state['state'] == 'error':
                    raise SystemExit(1)
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            monitor.stop()
            if not args.json:
                print()


if __name__ == '__main__':
    main()
