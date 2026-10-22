# Contributing

Small, focused fixes are welcome. Please describe the behavior you changed and
include a focused test when practical. Keep changes compatible with the
project's experimental use of Codex's internal IPC; that protocol can change
between app versions.

Run the offline checks from `plugins/codex-tps`:

```sh
python3 -m unittest -v test_server.py
python3 probe.py --self-test
node test_dashboard.mjs
```

From the repository root, also run `python3 -m unittest -v test_install.py`.

Mock tests and protocol self-tests do not prove that the plugin works with a
real Codex Desktop installation. Report real-host evidence separately, with
the app version and the behavior observed. Never include raw transcripts,
conversation or account IDs, or local file paths in public issues. Redact logs
before sharing them.
