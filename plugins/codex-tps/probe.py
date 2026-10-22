#!/usr/bin/env python3
"""Probe Codex Desktop's internal text stream without changing its app bundle.

Run in macOS Terminal while Codex Desktop is open:
  python3 probe.py --list
  python3 probe.py --thread THREAD_ID --seconds 90
Then request a long answer in that same conversation. The initial snapshot is
ignored; text_patch events prove incremental text delivery. No conversation text
is printed or saved. This verifies the stream, NOT token throughput: insert_chars
counts Unicode characters, not tokens. The internal IPC protocol may change.

Offline checks:
  python3 probe.py --inspect
  python3 probe.py --self-test
"""

import argparse
import collections
import io
import json
from pathlib import Path
import socket
import sqlite3
import struct
import time
import uuid

MAX_FRAME = 268435456
FOLLOW = "thread-stream-following-changed"
STREAM = "thread-stream-state-changed"


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def frame(value):
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError("invalid IPC frame length")
    return struct.pack("<I", len(data)) + data


def exact_read(reader, size):
    parts = bytearray()
    while len(parts) < size:
        block = reader.read(size - len(parts))
        if not block:
            raise EOFError("IPC connection closed mid-frame")
        parts.extend(block)
    return bytes(parts)


def read_message(reader):
    size = struct.unpack("<I", exact_read(reader, 4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("invalid IPC frame length")
    value = json.loads(exact_read(reader, size))
    if not isinstance(value, dict):
        raise ValueError("IPC message must be an object")
    return value


def connect_ipc(sock, home):
    path = home / "ipc" / "ipc.sock"
    try:
        sock.connect(str(path))
    except OSError as exc:
        # AF_UNIX errors omit the path; keep it in both CLI and panel diagnostics.
        raise OSError(exc.errno, exc.strerror, str(path)) from exc


class Measurements:
    def __init__(self):
        self.owner = None
        self.revision = None
        self.snapshots = 0
        self.patches = 0
        self.text_patches = 0
        self.insert_chars = 0
        self.last_text_at = None

    def consume(self, msg, thread, host, now):
        if msg.get("type") != "broadcast" or msg.get("method") != STREAM:
            return None
        p = msg.get("params", {})
        if p.get("conversationId") != thread or p.get("hostId") != host:
            return None
        if msg.get("version") != 11:
            raise ValueError("unsupported stream version; rerun --inspect")
        c = p["change"]
        if c["type"] == "snapshot":
            self.owner = msg.get("sourceClientId")
            self.revision = c["revision"]
            self.snapshots += 1
            self.last_text_at = None
            return {"kind": "snapshot", "revision": self.revision}
        if c["type"] != "patches" or msg.get("sourceClientId") != self.owner:
            return None
        if c["revision"] == self.revision:
            return None  # Do not count replayed frames.
        if c["baseRevision"] != self.revision:
            raise ValueError("stream revision gap; reconnect to obtain a new snapshot")
        self.revision = c["revision"]
        self.patches += 1
        fields = collections.Counter()
        deleted = 0
        for change in c.get("acceptedTextChanges") or []:
            if change.get("type") != "text":
                continue
            name = change.get("target", {}).get("field", "unknown")
            for edit in change.get("edits", []):
                fields[name] += len(edit.get("insert", ""))
                deleted += edit.get("deleteCount", 0)
        count = sum(fields.values())
        if not count:
            return None
        gap = None if self.last_text_at is None else round((now - self.last_text_at) * 1000, 1)
        self.last_text_at = now
        self.text_patches += 1
        self.insert_chars += count
        return {"kind": "text_patch", "revision": self.revision,
                "insert_chars": count, "field_chars": dict(fields),
                "deleted_utf16_units": deleted, "gap_ms": gap}


def inspect_bundle(path):
    with open(path, "rb") as f:
        header = struct.unpack("<IIII", exact_read(f, 16))
        if not 0 < header[3] < 32 * 1024 * 1024:
            raise ValueError("unexpected ASAR header")
        tree = json.loads(exact_read(f, header[3]))
        build = tree["files"][".vite"]["files"]["build"]["files"]
        sources = []
        for name, info in build.items():
            if name.endswith(".js") and "offset" in info:
                f.seek(8 + header[1] + int(info["offset"]))
                sources.append(f.read(info["size"]).decode("utf-8"))
    checks = {
        "framing_uint32le": any("readUInt32LE(0)" in s for s in sources),
        "following_v1": any('"thread-stream-following-changed":1' in s for s in sources),
        "stream_v11": any('"thread-stream-state-changed":11' in s for s in sources),
        "text_edits": any("acceptedTextChanges" in s and "insert:r.delta" in s for s in sources),
        "follower_snapshot": any("handleThreadStreamFollowingChanged" in s and "sendConversationSnapshot" in s for s in sources),
    }
    emit("static_protocol_check", checks=checks, passed=all(checks.values()),
         note="Code inspection only; does not prove live subscription succeeds.")
    return 0 if all(checks.values()) else 1


def list_threads(home):
    # Read SQLite with mode=ro; never take ownership of or resume a thread.
    databases = list(home.glob("state_*.sqlite"))
    if not databases:
        raise FileNotFoundError("No local Codex state database found")
    database = max(databases, key=lambda p: int(p.stem.split("_")[-1]))
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute("""SELECT id, title FROM threads
            WHERE archived=0 AND (thread_source IS NULL OR thread_source!='subagent')
            ORDER BY recency_at_ms DESC LIMIT 10""").fetchall()
    for thread_id, title in rows:
        emit("thread", thread_id=thread_id, title=title)


def probe(args):
    stats = Measurements()
    client = "initializing-client"
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    def following(value):
        return {"type": "broadcast", "method": FOLLOW, "version": 1,
                "sourceClientId": client, "params": {
                    "conversationId": args.thread, "hostId": args.host,
                    "following": value}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        connect_ipc(sock, args.home)
        with sock.makefile("rb") as reader:
            sock.sendall(frame({"type": "request", "method": "initialize", "version": 0,
                "requestId": request_id, "sourceClientId": client,
                "params": {"clientType": "codex-tps-probe"}}))
            # Initialization may be interleaved with other clients' broadcasts.
            while time.monotonic() - started < 10:
                msg = read_message(reader)
                if msg.get("requestId") == request_id and msg.get("type") == "response":
                    if msg.get("resultType") != "success":
                        raise ValueError("IPC initialization rejected")
                    client = msg["result"]["clientId"]
                    break
            else:
                raise TimeoutError("IPC initialization timed out")
            sock.sendall(frame(following(True)))
            emit("following_sent", thread_id=args.thread, host=args.host,
                 instruction="Ask for a long answer in this same open conversation now.")
            # A socket timeout aborts the probe instead of corrupting a partial frame.
            sock.settimeout(args.seconds)
            deadline = time.monotonic() + args.seconds
            try:
                while time.monotonic() < deadline:
                    sock.settimeout(max(0.05, deadline - time.monotonic()))
                    msg = read_message(reader)
                    if msg.get("type") == "client-discovery-request":
                        sock.sendall(frame({"type": "client-discovery-response",
                            "requestId": msg["requestId"], "response": {"canHandle": False}}))
                    elif (msg.get("type") == "broadcast" and
                          msg.get("method") == "thread-stream-following-status-requested"):
                        p = msg.get("params", {})
                        if p.get("conversationId") == args.thread and p.get("hostId") == args.host:
                            sock.sendall(frame(following(True)))
                    result = stats.consume(msg, args.thread, args.host, time.monotonic())
                    if result:
                        emit(result.pop("kind"), elapsed_s=round(time.monotonic()-started, 3), **result)
            except (TimeoutError, KeyboardInterrupt):
                pass
            finally:
                try:
                    sock.sendall(frame(following(False)))
                except OSError:
                    pass
                emit("summary", elapsed_s=round(time.monotonic()-started, 3),
                     snapshots=stats.snapshots, patches=stats.patches,
                     text_patches=stats.text_patches, insert_chars=stats.insert_chars,
                     subscription_confirmed=stats.snapshots > 0,
                     stream_text_observed=stats.text_patches > 0,
                     note="Characters are not tokens; live generation must be observed to confirm real-time behavior.")
    if not stats.snapshots:
        emit("error", error_type="NoSnapshot", message="IPC initialized, but the desktop did not publish a snapshot. Open the selected conversation and retry; this app build may not expose its stream.")
        return 2
    return 0


def self_test():
    sample = {"unicode": "测试🙂", "type": "broadcast"}
    assert read_message(io.BytesIO(frame(sample))) == sample
    class ShortReader(io.BytesIO):
        def read(self, size):
            return super().read(min(size, 1))
    assert read_message(ShortReader(frame(sample))) == sample
    for bad in [b"\x00"*4, struct.pack("<I", MAX_FRAME+1), frame(sample)[:-1]]:
        try:
            read_message(io.BytesIO(bad))
        except (ValueError, EOFError):
            pass
        else:
            raise AssertionError("malformed frame accepted")
    stats = Measurements()
    def message(change, thread="test"):
        return {"type": "broadcast", "method": STREAM, "version": 11,
                "sourceClientId": "owner", "params": {
                    "conversationId": thread, "hostId": "local", "change": change}}
    snapshot = {"type": "snapshot", "revision": 5,
                "conversationState": {"text": "old history must not count"}}
    assert stats.consume(message(snapshot), "test", "local", 0)["kind"] == "snapshot"
    assert stats.insert_chars == 0
    patch = {"type": "patches", "baseRevision": 5, "revision": 6,
             "acceptedTextChanges": [{"type": "text", "target": {"field": "text"},
                "edits": [{"at": 0, "deleteCount": 0, "insert": "你好🙂"}]}]}
    assert stats.consume(message(patch, "other"), "test", "local", 1) is None
    assert stats.consume(message(patch), "test", "local", 1)["insert_chars"] == 3
    assert stats.consume(message(patch), "test", "local", 2) is None
    gap = dict(patch, baseRevision=99, revision=100)
    try:
        stats.consume(message(gap), "test", "local", 3)
    except ValueError:
        pass
    else:
        raise AssertionError("revision gap accepted")
    assert stats.text_patches == 1 and stats.insert_chars == 3
    emit("self_test", passed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=Path.home()/".codex")
    parser.add_argument("--thread", help="Target conversation UUID from --list")
    parser.add_argument("--host", default="local", help="IPC host id; local is the default desktop host")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--bundle", default="/Applications/ChatGPT.app/Contents/Resources/app.asar")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.inspect:
        return inspect_bundle(args.bundle)
    elif args.list:
        list_threads(args.home.expanduser().resolve())
    else:
        if not args.thread or not 1 <= args.seconds <= 3600:
            parser.error("supply --thread and --seconds between 1 and 3600")
        args.home = args.home.expanduser().resolve()
        uuid.UUID(args.thread)
        return probe(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, EOFError, KeyError, sqlite3.Error) as exc:
        emit("error", error_type=type(exc).__name__, message=str(exc))
        raise SystemExit(1)
