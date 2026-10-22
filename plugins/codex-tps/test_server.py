import json
import io
from contextlib import redirect_stdout
import subprocess
import sys
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch as mock_patch

import server
import probe


class FakeEncoder:
    def encode(self, text, **kwargs):
        # A pair can merge into one token: chunk counts must not be summed.
        return list(text.replace('ab', 'X'))


def event(change, thread='test'):
    return dict(type='broadcast', method='thread-stream-state-changed', version=11,
                sourceClientId='owner', params=dict(conversationId=thread, hostId='local', change=change))


def patch(base, revision, at, text, field='text'):
    return event(dict(type='patches', baseRevision=base, revision=revision, patches=[],
        acceptedTextChanges=[dict(type='text', key=dict(itemId='msg'), target=dict(field=field),
            edits=[dict(at=at, deleteCount=0, insert=text)])]))


def usage(output, reasoning):
    return dict(total=dict(outputTokens=output, reasoningOutputTokens=reasoning))


def usage_patch(base, value):
    return event(dict(type='patches', baseRevision=base, revision=base+1,
        patches=[dict(op='replace', path=['latestTokenUsageInfo'], value=value)]))


class Tests(unittest.TestCase):
    def test_reported_usage_includes_non_text_output_without_fabricating_tps(self):
        m = server.Meter(FakeEncoder())
        m.started = 0
        m.consume(event(dict(type='snapshot', revision=1, conversationState={
            'latestTokenUsageInfo': usage(100, 40)})), 'test', 0)
        self.assertEqual(m.status(0)['reported_output_tokens'], 0)
        update = usage_patch(1, usage(150, 50))
        update['params']['change']['patches'].append(dict(op='add', path=['items', 0],
            value=dict(type='mcpToolCall', id='tool', tool='example', arguments={'query': 'input'},
                       result={'content': 'A long tool result is not model output'})))
        m.consume(update, 'test', 1)
        state = m.status(1)
        self.assertEqual(state['reported_output_tokens'], 50)
        self.assertEqual(state['reported_reasoning_tokens'], 10)  # A subset, not 50 + 10.
        self.assertEqual(state['visible_tokens'], 0)
        self.assertEqual(state['tps'], 0)
        self.assertEqual(state['last_delta_age_s'], None)
        m.consume(update, 'test', 1.1)  # Replayed revision.
        m.consume(usage_patch(2, usage(150, 50)), 'test', 1.2)  # Repeated total.
        self.assertEqual(m.status(2)['usage_updates'], 1)
        self.assertEqual(m.status(2)['last_usage_age_s'], 1)
        m.consume(patch(3, 4, 0, 'ab'), 'test', 2)
        self.assertEqual(m.status(2)['visible_tokens'], 1)
        self.assertEqual(m.status(2)['reported_output_tokens'], 50)

    def test_reported_usage_does_not_need_tokenizer_or_count_an_unknown_baseline(self):
        m = server.Meter()
        m.consume(event(dict(type='snapshot', revision=1, conversationState={})), 'test', 0)
        self.assertIsNone(m.status(0)['reported_output_tokens'])
        m.consume(usage_patch(1, usage(100, 40)), 'test', 1)
        self.assertEqual(m.status(1)['reported_output_tokens'], 0)
        m.consume(usage_patch(2, usage(120, 45)), 'test', 2)
        self.assertIsNone(m.status(2)['tps'])
        self.assertEqual(m.status(2)['reported_output_tokens'], 20)
        self.assertEqual(m.status(2)['reported_reasoning_tokens'], 5)

    def test_reported_usage_resnapshots_resets_and_invalid_counters(self):
        m = server.Meter()
        m.update_usage(usage(100, 40), 0, baseline=True)
        m.update_usage(usage(150, 50), 1)
        m.consume(event(dict(type='snapshot', revision=10, conversationState={
            'latestTokenUsageInfo': usage(300, 100)})), 'test', 2)
        self.assertEqual(m.status(2)['reported_output_tokens'], 50)
        m.consume(usage_patch(10, usage(320, 105)), 'test', 3)
        m.consume(usage_patch(11, usage(5, 2)), 'test', 4)  # Counter reset, not new generation.
        self.assertEqual(m.status(4)['reported_output_tokens'], 70)
        self.assertEqual(m.status(4)['usage_resets'], 2)
        m.consume(usage_patch(12, usage(8, 3)), 'test', 5)
        self.assertEqual(m.status(5)['reported_output_tokens'], 73)
        self.assertEqual(m.status(5)['reported_reasoning_tokens'], 16)
        for bad in (None, [], {}, usage(True, 0), usage('10', 0), usage(-1, 0), usage(1, 2)):
            with self.subTest(value=bad):
                m.update_usage(bad, 6)
                self.assertIsNone(m.status(6)['reported_output_tokens'])
        m.update_usage(usage(1000, 500), 7)  # Re-establish a baseline after missing data.
        self.assertEqual(m.status(7)['reported_output_tokens'], 73)

    def test_panels_use_host_thread_and_keep_monitors_separate(self):
        ids = ['00000000-0000-0000-0000-000000000001',
               '00000000-0000-0000-0000-000000000002']
        monitors = {}
        def call(name, thread_id):
            return server.dispatch('tools/call', dict(name=name, arguments={},
                _meta=dict(threadId=thread_id)), monitors)['structuredContent']
        def start(monitor, thread_id):
            monitor.state = 'subscribed'
            return monitor.status()
        with mock_patch.object(server.Monitor, 'start', autospec=True, side_effect=start) as begin:
            self.assertEqual(call('codex_tps_start', ids[0])['thread_id'], ids[0])
            monitors[ids[0]].meter.total_chars = 123
            self.assertEqual(call('codex_tps_start', ids[1])['visible_chars'], 0)
            # Reopening a panel must preserve its existing measurement.
            self.assertEqual(call('codex_tps_start', ids[0])['visible_chars'], 123)
            self.assertEqual(begin.call_count, 2)
            self.assertEqual(call('codex_tps_stop', ids[1])['state'], 'stopped')
            self.assertEqual(call('codex_tps_status', ids[0])['state'], 'subscribed')
            self.assertEqual(call('codex_tps_status', ids[0])['visible_chars'], 123)
            call('codex_tps_start', ids[1])
            self.assertEqual(begin.call_count, 3)

    def test_missing_or_invalid_host_context_never_guesses_a_thread(self):
        for meta in ({}, {'threadId': 'invalid'}, {'threadId': 42}, [], None):
            with self.subTest(meta=meta), mock_patch.object(server, 'threads') as listing:
                monitors = {}
                reply = server.dispatch('tools/call', dict(name='codex_tps_start',
                    arguments={}, _meta=meta), monitors)
                self.assertTrue(reply['isError'])
                self.assertEqual(monitors, {})
                listing.assert_not_called()

    def test_explicit_thread_cannot_override_panel_context(self):
        current = '00000000-0000-0000-0000-000000000001'
        other = '00000000-0000-0000-0000-000000000002'
        monitors = {}
        reply = server.dispatch('tools/call', dict(name='codex_tps_start',
            arguments=dict(thread_id=other), _meta=dict(threadId=current)), monitors)
        self.assertTrue(reply['isError'])
        self.assertEqual(monitors, {})
        self.assertEqual(server.request_thread({}, dict(thread_id=current)), current)

    def test_probe_only_confirms_subscription_after_snapshot(self):
        for snapshot in (False, True):
            with self.subTest(snapshot=snapshot):
                replies = [dict(type='response', requestId='init', resultType='success',
                                result=dict(clientId='test-client'))]
                if snapshot:
                    replies.append(event(dict(type='snapshot', revision=1, conversationState={})))
                replies.append(TimeoutError())
                output = io.StringIO()
                args = SimpleNamespace(thread='test', host='local', home=server.HOME, seconds=1)
                with mock_patch.object(probe.socket, 'socket'), \
                     mock_patch.object(probe, 'read_message', side_effect=replies), \
                     mock_patch.object(probe.uuid, 'uuid4', return_value='init'), \
                     redirect_stdout(output):
                    code = probe.probe(args)
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(events[0]['event'], 'following_sent')
                summary = next(e for e in events if e['event'] == 'summary')
                self.assertEqual(summary['subscription_confirmed'], snapshot)
                self.assertFalse(summary['stream_text_observed'])
                self.assertEqual(code, 0 if snapshot else 2)

    def test_invalid_mcp_inputs_do_not_kill_server(self):
        messages = [dict(id=i, method='tools/call', params=dict(
            name='codex_tps_start', arguments=dict(thread_id=value)))
            for i, value in enumerate([42, [], None, 'not-a-uuid'])]
        messages += [dict(id=4, method='tools/call', params=[]),
                     dict(id=5, method='tools/call', params=dict(name='codex_tps_start', arguments=['bad'])),
                     [], dict(id=6, method='ping')]
        r = subprocess.run([sys.executable, str(server.ROOT/'server.py'), 'mcp'],
                           input='\n'.join(map(json.dumps, messages))+'\n',
                           text=True, capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        replies = [json.loads(line) for line in r.stdout.splitlines()]
        self.assertEqual(len(replies), len(messages))
        for reply in replies[:-1]:
            self.assertTrue('error' in reply or reply['result'].get('isError'))
        self.assertEqual(replies[-1]['result'], {})

    def test_monitor_requires_snapshot_after_real_handshake(self):
        # A socket pair checks framing and lifecycle without a running desktop.
        for send_snapshot in (False, True):
            with self.subTest(snapshot=send_snapshot):
                client, peer = socket.socketpair()
                done = threading.Event()
                errors = []
                thread_id = '00000000-0000-0000-0000-000000000001'
                def host():
                    try:
                        with peer, peer.makefile('rb', buffering=0) as reader:
                            hello = server.read_message(reader)
                            peer.sendall(server.frame(dict(type='response', requestId=hello['requestId'],
                                resultType='success', result=dict(clientId='test-client'))))
                            follow = server.read_message(reader)
                            self.assertTrue(follow['params']['following'])
                            if send_snapshot:
                                peer.sendall(server.frame(event(dict(type='snapshot', revision=1,
                                    conversationState={}), thread=thread_id)))
                            try:
                                while server.read_message(reader):
                                    pass
                            except EOFError:
                                pass
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        done.set()
                worker = threading.Thread(target=host, daemon=True)
                with mock_patch.object(server.socket, 'socket', return_value=client), \
                     mock_patch.object(server, 'connect_ipc'), \
                     mock_patch.object(server, 'SNAPSHOT_TIMEOUT', 0.1), \
                     mock_patch.dict(sys.modules, tiktoken=SimpleNamespace(get_encoding=lambda _: FakeEncoder())):
                    worker.start()
                    monitor = server.Monitor()
                    try:
                        monitor.start(thread_id)
                        deadline = time.monotonic()+3
                        expected = 'subscribed' if send_snapshot else 'error'
                        while monitor.status()['state'] != expected and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertEqual(monitor.status()['state'], expected)
                        if send_snapshot:
                            time.sleep(0.3)
                            self.assertEqual(monitor.status()['state'], 'subscribed')
                        else:
                            self.assertIn('no snapshot', monitor.status()['error'])
                    finally:
                        monitor.stop()
                    self.assertTrue(done.wait(2))
                    worker.join(timeout=2)
                    self.assertEqual(errors, [])

    def test_ipc_permission_error_keeps_socket_path(self):
        sock = Mock()
        sock.connect.side_effect = PermissionError(1, 'Operation not permitted')
        with self.assertRaises(PermissionError) as caught:
            server.connect_ipc(sock, server.HOME)
        self.assertEqual(caught.exception.errno, 1)
        self.assertEqual(caught.exception.filename, str(server.HOME/'ipc'/'ipc.sock'))

    def meter(self):
        m=server.Meter(FakeEncoder())
        m.started=0
        m.consume(event(dict(type='snapshot', revision=1, conversationState={
            'items':[dict(type='agentMessage',id='old',text='old history')]})), 'test', 0)
        return m

    def test_history_and_stream(self):
        m=self.meter()
        self.assertEqual(m.status(0)['visible_tokens'],0)
        m.consume(patch(1,2,0,'a'),'test',1)
        m.consume(patch(2,3,1,'b'),'test',1.1)
        self.assertEqual(m.status(1.2)['visible_tokens'],1)
        self.assertEqual(m.status(1.2)['visible_chars'],2)
        self.assertEqual(m.status(4)['tps'],0)
        m.consume(patch(2,3,1,'b'),'test',4)
        self.assertEqual(m.text_updates,2)

    def test_tool_output_and_other_thread(self):
        m=self.meter()
        m.consume(patch(1,2,0,'tool log',field='aggregatedOutput'),'test',1)
        other=patch(2,3,0,'wrong thread')
        other['params']['conversationId']='other'
        m.consume(other,'test',2)
        self.assertEqual(m.total_chars,0)
        self.assertEqual(m.revision,2)

    def test_gap_fails_instead_of_fabricating_rate(self):
        m=self.meter()
        with self.assertRaises(ValueError):
            m.consume(patch(5,6,0,'x'),'test',1)

    def test_mid_message_snapshot_and_utf16(self):
        m=self.meter()
        m.consume(event(dict(type='snapshot',revision=2,conversationState={
            'items':[dict(type='agentMessage',id='msg',text='🙂')]})), 'test',1)
        m.consume(patch(2,3,2,'ab'),'test',2)
        self.assertEqual(m.texts['msg'],'🙂ab')
        self.assertEqual(m.total_tokens,1)
        self.assertEqual(m.total_chars,2)
        with self.assertRaises(ValueError):
            server.splice_utf16('x',5,0,'y')

    def test_no_tokenizer_never_labels_characters_as_tokens(self):
        m=server.Meter()
        self.assertIsNone(m.status()['tps'])
        self.assertIsNone(m.status()['visible_tokens'])

    def test_mcp_subprocess_discovery_and_ui_resource(self):
        messages=[dict(jsonrpc='2.0', id=1, method='initialize', params={}),
                  dict(jsonrpc='2.0', method='notifications/initialized'),
                  dict(jsonrpc='2.0', id=2, method='tools/list'),
                  dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='codex_tps_status',arguments={},
                       _meta=dict(threadId='00000000-0000-0000-0000-000000000001'))),
                  dict(jsonrpc='2.0', id=4, method='resources/read',params=dict(uri=server.UI))]
        r=subprocess.run([sys.executable,str(server.ROOT/'server.py'),'mcp'],
            input='\n'.join(map(json.dumps,messages))+'\n',text=True,capture_output=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        replies=[json.loads(line) for line in r.stdout.splitlines()]
        self.assertEqual([x['id'] for x in replies],[1,2,3,4])
        self.assertEqual(len(replies[1]['result']['tools']),5)
        self.assertEqual(replies[2]['result']['structuredContent']['state'],'stopped')
        self.assertEqual(replies[3]['result']['contents'][0]['mimeType'],'text/html;profile=mcp-app')


if __name__=='__main__':
    unittest.main()
