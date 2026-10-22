import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('./dashboard.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function mockHost() {
  const elements = Object.fromEntries(['speed', 'unit', 'tokens', 'updates', 'age', 'reported-output', 'reported-reasoning', 'usage-state', 'state', 'error', 'line', 'start', 'stop'].map(id => [id, {
    textContent: '', disabled: false, onclick: null, attributes: {},
    setAttribute(key, value) { this.attributes[key] = value; },
  }]));
  const calls = [];
  const parent = { postMessage(message) { calls.push(message); } };
  let onMessage, poll, intervalMs;
  const context = {
    parent,
    window: { addEventListener(type, fn) { if (type === 'message') onMessage = fn; } },
    document: {
      hidden: false,
      getElementById: id => elements[id],
      documentElement: { style: { setProperty() {} } },
      body: { style: {} },
    },
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval(fn, ms) { poll = fn; intervalMs = ms; },
  };
  vm.runInNewContext(script, context, { filename: 'dashboard.html' });
  return {
    calls, elements, parent,
    get onMessage() { return onMessage; },
    get poll() { return poll; },
    get intervalMs() { return intervalMs; },
    reply(request, result, error) {
      onMessage({ source: parent, data: { jsonrpc: '2.0', id: request.id, result, error } });
    },
  };
}

const turn = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 6; i++) await turn(); }
const requests = (host, name) => host.calls.filter(m => m.method === 'tools/call' && m.params.name === name);
const status = (state, values = {}) => ({ state, tps: 2, visible_tokens: 4, text_updates: 1, last_delta_age_s: null, reported_output_tokens: null, reported_reasoning_tokens: null, usage_updates: 0, last_usage_age_s: null, usage_resets: 0, ...values });

{
  const host = mockHost();
  const init = host.calls.find(m => m.method === 'ui/initialize');
  assert.equal(init.params.appInfo.version, '0.1.4');
  host.reply(init, { hostInfo: { name: 'Codex', version: '26.1016.61520' }, hostContext: {} });
  await settle();
  let starts = requests(host, 'codex_tps_start');
  assert.equal(starts.length, 1, 'start runs automatically after initialization');
  assert.deepEqual(JSON.parse(JSON.stringify(starts[0].params.arguments)), {});
  assert.equal(requests(host, 'codex_tps_threads').length, 0);
  assert.equal(host.elements.state.textContent, 'Connecting to this conversation…');
  assert.equal(host.elements.start.disabled, true);
  assert.equal(host.elements.stop.disabled, false);
  host.reply(starts[0], { isError: true, content: [{ type: 'text', text: 'No active conversation' }] });
  await settle();
  assert.equal(host.elements.state.textContent, 'Connection failed');
  assert.equal(host.elements.error.textContent, 'No active conversation');
  assert.equal(host.elements.start.disabled, false, 'a failed start can be retried');
  assert.equal(host.elements.stop.disabled, true);

  host.elements.start.onclick();
  await settle();
  starts = requests(host, 'codex_tps_start');
  assert.equal(starts.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(starts[1].params.arguments)), {});
  host.reply(starts[1], { structuredContent: status('subscribed', { tps: null, chars_per_second: 7.5 }) });
  await settle();
  assert.equal(host.elements.start.disabled, true);
  assert.equal(host.elements.stop.disabled, false);
  assert.equal(host.elements.speed.textContent, '7.5', 'reported counts do not replace visible text speed');
  assert.equal(host.elements.unit.textContent, 'chars/s (no tokenizer)');
  assert.equal(host.elements['reported-output'].textContent, '—');
  assert.equal(host.elements['reported-reasoning'].textContent, '—');
  assert.equal(host.elements['usage-state'].textContent, 'Waiting for reported usage');

  assert.equal(host.intervalMs, 500);
  const poll = host.poll();
  await settle();
  const pollCalls = requests(host, 'codex_tps_status');
  assert.equal(pollCalls.length, 1);
  host.reply(pollCalls[0], { structuredContent: status('subscribed', { tps: null, chars_per_second: 7.5, reported_output_tokens: 124, reported_reasoning_tokens: 36 }) });
  await poll;
  assert.equal(host.elements.speed.textContent, '7.5', 'reported output counts stay separate from TPS');
  assert.equal(host.elements['reported-output'].textContent, 124);
  assert.equal(host.elements['reported-reasoning'].textContent, 36);
  assert.equal(host.elements['usage-state'].textContent, 'Waiting for the next usage update');

  const usagePoll = host.poll();
  await settle();
  const usageCalls = requests(host, 'codex_tps_status');
  assert.equal(usageCalls.length, 2);
  host.reply(usageCalls[1], { structuredContent: status('subscribed', { tps: null, chars_per_second: 7.5, reported_output_tokens: 150, reported_reasoning_tokens: 42, usage_updates: 2, last_usage_age_s: 1.2, usage_resets: 1 }) });
  await usagePoll;
  assert.equal(host.elements['reported-output'].textContent, 150);
  assert.equal(host.elements['reported-reasoning'].textContent, 42);
  assert.equal(host.elements['usage-state'].textContent, '2 usage updates · 1.2s ago · Counter reset; measurement excludes the gap');

  host.elements.stop.onclick();
  await settle();
  const stops = requests(host, 'codex_tps_stop');
  assert.equal(stops.length, 1);
  host.reply(stops[0], { structuredContent: status('stopped') });
  await settle();
  assert.equal(host.elements.start.disabled, false);
  assert.equal(host.elements.stop.disabled, true);

  host.elements.start.onclick();
  await settle();
  starts = requests(host, 'codex_tps_start');
  assert.equal(starts.length, 3, 'Start resumes after Stop');
  assert.equal(host.elements.line.attributes.d, 'M0,85', 'restart clears the graph');
  assert.deepEqual(JSON.parse(JSON.stringify(starts[2].params.arguments)), {});
  host.reply(starts[2], { structuredContent: status('connecting') });
  await settle();
  assert.equal(requests(host, 'codex_tps_threads').length, 0);
}

{
  const host = mockHost();
  host.reply(host.calls.find(m => m.method === 'ui/initialize'), undefined, { message: 'Host initialization failed' });
  await settle();
  assert.equal(host.elements.state.textContent, 'Connection failed');
  assert.equal(host.elements.error.textContent, 'Host initialization failed');
  assert.equal(host.elements.start.disabled, true);
  assert.equal(requests(host, 'codex_tps_start').length, 0);
}

console.log('dashboard mock-host checks passed');
