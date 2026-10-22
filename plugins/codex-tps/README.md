# Codex TPS plugin

Experimental MCP App panel and shell command for live visible-text speed and
reported model output, including tool calls and reasoning. Real incremental delivery and CLI token rates were verified on
Codex Desktop 26.1016.61520 after a normal app restart.

From this directory:

```sh
./bin/codex-tps threads
./bin/codex-tps watch --thread <conversation-uuid>
./bin/codex-tps watch --thread <conversation-uuid> --json
```

Requires macOS, a running local Codex Desktop, Python 3.11+, and uv. The launcher
uses `tiktoken` with `o200k_base`; first use may download dependencies and the
vocabulary. Direct `python3 server.py watch --thread UUID` works without the
optional tokenizer and reports characters per second in that case. Reported
output usage remains available without a tokenizer.

## Panel tools

| Tool | Purpose |
| --- | --- |
| `codex_tps_open` | Open the conversation panel |
| `codex_tps_threads` | List recent local conversations |
| `codex_tps_start` | Subscribe to the containing conversation |
| `codex_tps_status` | Read that conversation's meter without a model turn |
| `codex_tps_stop` | Stop that conversation's subscription |

Open the panel in a conversation; it starts observing that conversation automatically.
The panel polls every 500 ms. A missing initial snapshot fails after 10 seconds.
Each conversation has a separate meter, resolved from the host's `_meta.threadId`.
Missing or invalid context fails instead of selecting another conversation.
Use **Stop** to release the subscription and **Start observing** to restart it.
Panels for the same conversation share a meter; reopening an active one preserves
its counts. Closing a panel alone does not stop that shared meter.

## Measurement and privacy

The two-second rolling rate estimates net token growth in visible replies and
plan text. History, tool output, input, and reasoning are excluded. Text edits use
JavaScript UTF-16 offsets. Missing tokenization produces `null` token metrics,
never a character count labeled as tokens. This is not official usage or precise
server decoding speed. Internal IPC can change between desktop versions.

The separate **Reported model output** block reads the desktop's output usage
counters, including tool calls and reasoning. The reasoning count is a subset
of output, not an additional amount. Counts accumulate after a usage baseline;
history is excluded. A report can cover a request already in progress when the
monitor connected. Missing data displays as unavailable; counter resets and
resnapshots establish a fresh baseline and exclude the gap. These counters do
not depend on tiktoken and are not a billing statement.

Usage arrives in batches, so it is not mixed into live TPS. The IPC does not
provide a verified token-by-token tool-argument stream. Tool results and file
diffs are not used as proxies for generated tokens. A tool-only token count or
complete model decoding speed cannot be recovered from this feed.

Conversation metadata is read through a read-only SQLite connection. Text stays
in memory; the plugin does not save it or send it to a third party. Diagnostic
output contains metadata only. Keep conversation titles and IDs private.

## Checks

```sh
python3 -m unittest -v test_server.py
python3 probe.py --self-test
python3 probe.py --inspect
node test_dashboard.mjs
python3 probe.py --thread <conversation-uuid> --seconds 120
```

`--inspect` checks the installed app's code, not live behavior. In a real probe,
`following_sent` is not a confirmed subscription. Require `snapshot`, then
multiple visible `text_patch` events before a long reply ends. No snapshot exits
`2`; transport/protocol errors exit `1`. If the selected conversation is open but
no snapshot arrives, restart Codex Desktop normally and retry. See the repository's
root README and SESSION for installation steps and real panel verification status.
