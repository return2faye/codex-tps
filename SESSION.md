# Development record

Updated: 2026-10-23, Asia/Singapore. Project documentation and UI are English-first.

## Takeover and source integrity

The project continues the supplied `codex-tps-dev` source tree. It was not rewritten
or restored from the older ZIP. On arrival, the destination already contained
13 maintained files, including later README and session notes and a socket-error
diagnostic fix. Those destination changes were retained.

Before editing, all 13 files were backed up byte for byte to
`.local/takeover-original/`, with a SHA-256 inventory. That ignored directory also
preserves the original Chinese notes. The original external source tree was left
untouched. Comparison against it found six unchanged files and six changed files;
the standard marketplace file was missing. That file was restored to
`.agents/plugins/marketplace.json` and matched both the source and the destination's
root-level copy byte for byte. The redundant root copy was then removed; its
original remains in the takeover backup.

All maintained code, documentation, and tests now live in this repository. No
remote repository, release, or public plugin submission was created.

## Earlier work retained as history

The original investigation inspected the desktop ASAR and identified UInt32LE
length-prefixed JSON framing, `initialize`, following v1, and stream-state v11.
It implemented a Python meter, stdio MCP server, shell launcher, and MCP App panel.
The original six tests passed; a later takeover added a socket-path diagnostic
test, bringing the starting suite to seven. Earlier notes also report a successful
mock-host panel exercise and JavaScript syntax check.

Those earlier sessions could not connect to the IPC socket: `connect` returned
`PermissionError`, errno 1. A previous attempt also could not create `.agents`
or use uv's default cache. These were historical environment results. They do not
describe this takeover's permissions, and none established a working live panel.

## Real-machine observations before restart

Environment: `/Applications/ChatGPT.app`, version `26.1016.61520`, build `21421`;
Codex CLI `0.154.0`; uv `0.11.19`; Python `3.14.2`.

The current environment allowed normal socket access and creation of the standard
marketplace directory. No permission settings, app files, feature gates, or
conversation database records were changed.

- The initial seven tests, probe self-test, and static bundle checks passed.
- The real socket accepted a connection and `initialize` returned a client ID.
- A following v1 broadcast targeting the current open local conversation received
  no snapshot. Separate 10-second and 45-second probes also saw no text patches.
- A further 40-second probe covered a long visible progress response in that
  conversation: 0 snapshots, 0 patches, and 0 inserted characters. It exited `2`
  after the diagnostic correction. This attempt cannot verify streaming or TPS.
- A read-only `thread-owner-discovery` v1 request returned `no-client-found`
  after 10.002 seconds. An earlier shorter diagnostic had timed out locally.
- Desktop logs recorded the target conversation as `role=owner`. IPC log entries
  also reported a received following broadcast with no handler configured.
  There are multiple IPC clients; these log lines do not identify the missing
  runtime route conclusively.
- The uv launcher listed 30 local conversations. `watch --json` loaded the
  `o200k_base` tokenizer, emitted 35 samples across connecting, snapshot-wait,
  and error states, then exited `1` with the new snapshot-timeout message.

Static review confirmed that the outgoing fields match the installed bundle.
The router forwards untargeted broadcasts to other registered clients. Snapshot
publication requires a manager that owns the conversation and has stream handlers
registered. Renderer owner-discovery registration is conditional on whether the
main-process manager service is present. The relevant runtime registration state
has not been established; this is a diagnostic lead, not a confirmed root cause.

The desktop's native UI was unavailable to computer-use tooling because that
surface is blocked by the tool. No alternate UI automation was attempted.
No marketplace was registered and no plugin was installed, following the requested
order of verifying the data channel first.

## Changes made after reproduction

- Restored the standard marketplace path without overwriting destination changes.
- Validated MCP request objects, parameter objects, and UUID inputs. Invalid
  `thread_id` types previously raised an uncaught `AttributeError` and killed the
  stdio service; errors now leave the service available for the next request.
- Canonicalized valid UUID strings before subscribing.
- Added a 10-second initial snapshot deadline. An established idle subscription
  remains valid; only the missing baseline times out.
- Changed the probe's premature `subscribed` event to `following_sent`, added
  `subscription_confirmed` and elapsed time to its summary, and made a missing
  snapshot exit with code `2`.
- Translated the existing dashboard and maintained documentation into English.
  Layout, polling interval, metric definition, and tool names were retained.
- Added MIT licensing, contribution guidance, ignore rules, and offline CI.

The suite now has ten tests. New checks cover malformed MCP inputs without process
death, the distinction between a sent request and a received snapshot, and a
socket-pair handshake with idle-stream and connection-cleanup behavior. These are
controlled fixtures, not a successful real desktop subscription.

## After the user's normal desktop restart

The desktop version did not change. A fresh 45-second probe received its snapshot
after 16 ms and five patch batches. The protocol and socket permissions were not
modified. This establishes that the earlier missing feed was recoverable by a
normal desktop restart; it does not establish the underlying routing defect.

A second probe and the actual uv-backed `watch --json` ran concurrently while a
long visible reply was generated in the selected conversation:

- The 120-second probe received one snapshot and 245 patch batches.
- It observed 178 text-edit events across all fields, including reasoning
  summaries. Only 171 events edited the visible `text` field: 900 characters.
- Visible edits spanned 22.390 through 58.088 seconds of the probe. Median interval
  between those edits was 71.5 ms. The long reply arrived in many patches while
  it was being generated, rather than in one completion batch.
- At the end of that measurement the CLI reported 171 updates, 900 characters,
  and 594 estimated visible tokens. Peak rolling rate was 46.0 tok/s. Counts
  matched the visible-text probe fields; reasoning-summary fields were excluded.
- The CLI stayed subscribed while idle and decayed to 0 tok/s. No revision gaps,
  skipped items, or connection errors appeared. Sending SIGINT to this test's
  process released the connection and returned exit code 0.

Metadata-only evidence is retained in ignored `.local/live-stream-probe.jsonl`
and `.local/live-watch.jsonl`. These files contain conversation identifiers and
must not be published. The watch continued briefly after the probe, so later
watch totals can include subsequent progress messages.

## Installation and cached service checks

Registered the local marketplace using `codex plugin marketplace add .`, then
installed through `codex plugin add codex-tps@codex-tps-dev --json`. CLI discovery
resolved the portable manifest's `${PLUGIN_ROOT}` to the versioned plugin cache.
No cache files were manually edited.

Version 0.1.1 updates the connection error with the observed restart recovery,
uses the short panel title `Codex TPS`, removes obsolete unverified-feed text,
and makes the compatibility MCP paths explicit with `${PLUGIN_ROOT}`.

Added `python3 install.py` to install both the plugin and a shell wrapper in
`~/.local/bin`. The wrapper executes the cached launcher, with shell quoting for
paths containing spaces. It is not a symlink: the existing launcher locates its
server relative to its own location. The installer refuses to replace unrelated
commands and symlinks. A review caught a wrapper-marker mismatch that would have
blocked reinstallation; it was fixed and covered with a repeat-install test.

- All five installer tests passed; the original ten server tests still pass.
- The real one-command installer was run twice successfully for 0.1.1.
- `codex-tps threads` ran successfully from outside the repository.
- The installed `codex-tps watch` also subscribed from outside the repository in
  a PTY, refreshed while idle, and exited with code 0 on Ctrl-C.
- A subprocess launched using Codex's resolved installed MCP configuration
  reported version 0.1.1, exposed all five tools, and returned its HTML resource.
- That installed service received a real snapshot. In 60 status samples over
  30 seconds, it counted seven visible updates and 67 estimated tokens, peaking
  at 33.5 tok/s. `codex_tps_stop` returned `stopped`; closing stdin exited cleanly.
- Probe self-test, current bundle inspection, and dashboard JavaScript syntax
  checks passed after these changes.

The installed-service check is a real subprocess and real IPC check, not a
rendered desktop-panel check. Its metadata-only summary is in ignored
`.local/installed-mcp-check.json`.

## Desktop panel confirmation and 0.1.2

The user supplied a desktop screenshot of the installed panel during a reply.
It shows `Receiving text updates`, approximately 11.5 tok/s, 63 visible tokens,
27 updates, a last-update age of 0.2 s, and a populated speed graph. This is real
host evidence of panel rendering and live reception, supplied by the user.

At the user's request, 0.1.2 removes the Refresh sessions button and its event
handler. Sessions still load automatically during initialization; reopening the
panel loads a fresh list. Start observing, Stop, and metric polling are retained.
The UI change has JavaScript syntax and installed-resource checks.

The computer-use tool blocks the Codex main window, and the screenshot's panel
is in another task, outside this task's MCP Apps browser surface. Direct automated
panel interaction, Stop-button behavior in the real host, visual confirmation of
0.1.2, app updates or other OS versions, and hosted CI remain unverified. The
internal stream protocol remains unstable.

## Conversation-bound panel in 0.1.3

The user asked to remove session selection: a panel should measure the
conversation where it is opened. The session picker and session-list calls were
removed. After MCP App initialization, the panel calls `codex_tps_start` with
empty arguments automatically. Start and Stop remain for restarting or stopping
that conversation's measurement.

The containing conversation is supplied by Codex in each tool request's
`_meta.threadId`. It is not a field in `ui/initialize.hostContext`. Inspection of
the installed widget code established the renderer-to-app-server mapping; the
official [MCP request processor](https://github.com/openai/codex/blob/main/codex-rs/app-server/src/request_processors/mcp_processor.rs)
inserts the request's thread ID into MCP metadata before dispatch.

The server now keeps one monitor per conversation rather than a process-wide
current monitor. Start is idempotent while that monitor is active. Status and
Stop use the same per-request context; another conversation's panel cannot reset
or stop it. Missing or invalid context fails explicitly, without looking at
recency or the active window. An explicit thread argument cannot override host
context. The shell's `watch --thread UUID` interface is unchanged.

Validation:

- Thirteen Python server tests passed, including two-conversation isolation,
  repeated Start, missing/malformed context, and conflicting explicit IDs.
- The Node mock-host test passed for automatic start, polling, Stop/restart,
  startup errors and retries, and initialization failure. It runs the panel's
  actual inline script and is included in offline CI.
- A real app-server test used two ephemeral test threads, without model turns.
  Both the shell CLI and the desktop's bundled backend injected the correct
  `_meta.threadId`, including when a caller-supplied value was deliberately
  wrong. Each returned the correct stopped monitor. Test threads were unloaded
  and test app-server processes exited normally.
- Installed 0.1.3 through the existing installer. The cached HTML matches source
  and contains neither a session picker nor session-list requests.
- The installed MCP service, called with empty arguments and conversation
  metadata, received a real desktop snapshot and two visible updates (peak
  rolling rate 36 tok/s). Repeated Start preserved counts; stopping a different
  conversation left the active subscription intact. Final Stop and exit passed.

An initial app-server diagnostic with test-only MCP overrides received no
initialization reply. Its process-group cleanup was denied; no permission change
or forced cleanup was used. The revised harness used normal configuration,
closed stdin for shutdown, and completed successfully. Persistent Codex
configuration was not changed for these diagnostics.

Metadata-only results are in ignored `.local/host-context-check.json` and
`.local/context-live-check.json`. Visual confirmation of the 0.1.3 panel in the
desktop remains pending; the previous user screenshot verifies 0.1.1 rendering.

## Reported model output in 0.1.4

The user correctly identified that visible replies are only part of model
output: tool calls also consume generated output. Investigation of the actual
IPC feed found complete `commandExecution` and `mcpToolCall` objects at invocation
time, rather than a verified stream of raw tool-argument token deltas. MCP tool
arguments remained unchanged when results arrived. Inspection also found that a
`fileChange` deletion can contain the deleted file's text, and update diffs can
contain regenerated context. Tokenizing these projections would count text that
cannot reliably be attributed to new model output. No such estimator was added.

A better source is `latestTokenUsageInfo` in the conversation snapshot. Desktop
`thread/tokenUsage/updated` notifications store `total` and `last` usage buckets
there; the live IPC feed publishes the complete object at the root patch path
`["latestTokenUsageInfo"]`. The `total.outputTokens` counter includes reasoning;
`total.reasoningOutputTokens` is a breakdown, not another quantity to add. The
official [output-token guide](https://developers.openai.com/api/docs/guides/token-counting)
and [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
describe output usage beyond visible text. This is host-reported usage, not an
independently verified billing statement.

The panel now explicitly labels its rolling rate **Visible text speed** and adds
**Reported model output** with output and included reasoning counts. The CLI
prints the same distinction; JSON adds `reported_output_tokens`,
`reported_reasoning_tokens`, `usage_updates`, `last_usage_age_s`, and `usage_resets`.
Reported counts do not require tiktoken. They are not fed into the TPS graph:
batched receipt times do not reveal hidden or tool-token generation times.

Usage snapshots establish baselines. Identical totals and replayed frames add
nothing. Missing/invalid counters are unavailable. A reset or resnapshot
re-establishes the baseline, preserves previously measured increments, and marks
the excluded gap. When the initial snapshot has no usage, the first valid report
becomes the baseline and is not counted. A request already in progress can later
report tokens generated before the monitor connected; the labels and README
therefore describe newly reported usage, not an exact generation-time boundary.

Validation on the same desktop build:

- A 75-second live diagnostic received four usage updates with no visible-text
  updates: output increased by 1,349 tokens, including 746 reasoning tokens.
- Sixteen server tests passed, including history, repeated counters, revision
  replay, reset/resnapshot/malformed data, no-tokenizer operation, and separation
  of batched usage from the live TPS calculation.
- The panel mock-host test passed, including unavailable/pending usage,
  reasoning as a subset, tokenizer fallback, and a counter-reset message.
- Five installer tests, the probe self-test, and current bundle inspection passed.
- Installed 0.1.4 through the normal installer. In a 45-second real IPC test, the
  installed MCP service and shell command independently observed identical
  increments: 2,577 reported output tokens, including 914 reasoning tokens, in
  four usage updates. Their visible-text estimate was 101 tokens. Both observed
  usage while the visible rate was zero; neither recorded a usage gap. MCP Stop
  and CLI SIGINT completed, and both processes exited with status 0.

Metadata-only diagnostics are in ignored `.local/tool-stream-shapes.jsonl`,
`.local/tool-usage-timing.jsonl`, and `.local/reported-usage-check.json`.
Full model decoding TPS and a tool-only token count remain unavailable from this
feed. The 0.1.4 panel's actual desktop rendering remains unverified; the mock-host
test and installed HTML checks do not replace that check.

### Throughput-curve icon (2026-10-23, Asia/Singapore)

At the user's request, a subagent created a bundled SVG icon: a teal throughput
curve on a dark rounded tile. The artwork is illustrative, not a captured data
series. Both plugin manifests reference `assets/codex-tps-icon.svg` through
`interface.logo` and `interface.composerIcon` (under `extensions.com.openai` in
the portable manifest). The subagent rendered and inspected the 512 px artwork.
Version 0.1.4 includes this icon with the usage changes. Actual desktop icon
rendering still needs confirmation after the plugin is reloaded.

### Public README and live demonstration (2026-10-23)

The English README now includes the bundled icon and `docs/images/live-demo.jpg`.
The screenshot is an unretouched browser preview of the actual dashboard, using
a local test host that forwards its tool calls to the real meter and Codex Desktop
IPC. It shows approximately 16.5 visible tok/s, 168 estimated visible tokens,
72 text updates, and 1,913 reported output tokens including 1,454 reasoning tokens.
No metric data was synthesized. The image contains no conversation title, ID,
account details, or transcript. It is explicitly labeled as a browser preview,
not a native Codex window capture. The actual Stop button stopped observation;
the preview tab and its temporary server were closed cleanly afterward.

Distribution reuses Codex's existing GitHub marketplace support. CLI 0.154.0
accepts `OWNER/repo` as a marketplace source, followed by
`codex plugin add codex-tps@codex-tps-dev`. The README documents this plugin-only
route and, as a separate alternative, the existing checkout installer for both
the plugin and shell command. It also covers prerequisites, updates, removal,
measurement limits, and troubleshooting. The marketplace ID remains unchanged
to preserve existing installations. No extra installer or runtime dependency was
added for distribution.

The repository has no configured Git remote or confirmed publication URL, so
the README uses `OWNER/codex-tps` as an explicit placeholder. Public GitHub
installation and hosted CI remain untested. Source files, tests, manifests,
documentation, and the demo image are prepared for the initial local commit;
private diagnostic evidence and the takeover backup remain ignored in `.local/`.

### Public repository and README humor (2026-10-23)

The user published `return2faye/codex-tps` on GitHub. Repository metadata confirms
public visibility, and remote `main` contains the initial commit `f576c1e`.
The README installation examples now use that repository instead of `OWNER`.
This confirms publication, not a complete installation test on another machine.

At the user's request, the README adds the English section "Trust Tibo. Verify
tok/s." and an existing Saint Tibo community meme. The image is embedded from
its external image URL, with a link to the Saint Tibo fan archive where it was
found; the image is not bundled or relicensed under this repository's MIT license.
The copy jokes about watching the speed dial while retaining the distinction
between observing visible-text speed and proving throttling or its cause.
No runtime behavior changed.
