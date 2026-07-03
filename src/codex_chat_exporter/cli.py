#!/usr/bin/env python3
"""Export local Codex session JSONL files to readable Markdown."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


PRESET_ALIASES = {
    "full": "full",
    "readable": "readable",
    "decisions": "decisions",
    "trace": "trace",
    "raw-jsonl": "raw-jsonl",
    "jsonl": "raw-jsonl",
    "raw": "full",
    "clean": "readable",
    "substantive": "decisions",
}

PRESET_LABELS = {
    "full": "full visible transcript (legacy raw): all visible user/assistant text minus internal context tags",
    "readable": "readable (legacy clean): full chat with obvious noise removed",
    "decisions": "decisions (legacy substantive): compact conclusion/evidence extract",
    "trace": "execution trace: all JSONL records rendered as Markdown, with bulky internal blobs omitted",
    "raw-jsonl": "raw JSONL: exact original session JSONL, including tool calls and internal records",
}

ENV_KEYWORDS = [
    "cvd", "cuttlefish", "cgdroid", "arm-2", "r743", "adb reboot", "adb sync",
    "lmy", "避让", "磁盘空间", "sleep脚本", "sleep命令", "sync部署",
    "image部署", "build deploy", "harness flake", "system.img",
]

UE_KEYWORDS = [
    "lxr_user_events", "user_events", "user-events", "perfetto", "traced_probes",
    "tracefs", "atrace", "trace-cmd", "bpftrace", "pftrace", ".pftrace",
    "trace_event_buffer", "trace_event_class", "register_trace_event",
    "trace_add_event_call", "enable_addr", "enable_bit", "lxr_write",
    "lxr_oaram_writer", "v5.18", "v6.18", "v7.1", "5.18内核",
    "5.18 内核", "6.18 ABI", "aosp_test_prep", "kernel_build",
    "kernel_deploy", "lxr_aosp11_cf_arm64_deploy", "savedefconfig",
    "GenericEventDescriptor", "GenericFtraceEvent", "CreateGenericEvent",
    "lxr_user_events_parser", "libtracefs", "CONFIG_USER_EVENTS", "shm_ring",
    "shm_event", "shm_emit", "ftrace 基础架构", "ftrace基础架构", "插装系统",
    "插装基础设施", "per-cpu", "per cpu", "兼容性盘点", "调试 workflow",
    "调试workflow", "build + deploy + reboot", "build+deploy+reboot",
    "ftrace per cpu", "ftrace per-cpu", "一整套生态链", "生态链", "怎么移植",
    "ABI 移植", "ABI移植", "ftrace ring", "frace子", "user_events_mmap",
    "美美哒", "接口兼容", "实现的原理", "现在的原理", "你的方法",
    "实现上尽量简单", "部署art的问题", "部署 art 的问题", "搜一下聊天记录",
    "tmd两天前", "ftrace", "userspace", "write_index", "user_reg",
    "status_index", "status_data", "关键设计", "跳过的部分", "工作原理",
    "不能用的", "与标准不兼容", "关键结论",
]

SUBSTR_DROP = [
    "[Request interrupted by user]",
    "[Request interrupted by user for tool use]",
    "This session is being continued from a previous conversation",
    "No response requested",
]

USER_DROP_RE = re.compile(
    r"^(continue|go|好的|ok|你再干嘛|TMD跑啊|哎沃日.*|草你妈.*|"
    r"重大转折.*|不是你有病.*|你脑子有.*|破坏性你妈.*|请使用/|抱歉.*|"
    r"还没up.*|怎么样了.*|\[image\]|go$|好的，话说.*)$",
    re.IGNORECASE,
)

VERDICT_RE = re.compile(
    r"(✅|🎯|❌|⚠️|🔥|🚨|verdict|判定|结论|总结|root[ -]?cause|根因|"
    r"证据齐|证据已|fingerprint|指纹|证伪|证实|自证|hypothesis|假设|"
    r"first[- ]bad|关键发现|重大发现|关键结论|核心结论|当前判断|当前状态|"
    r"当前判定|当前结论|含义|结论是|结论：)",
    re.IGNORECASE,
)

INTERNAL_CONTEXT_BLOCK_RE = re.compile(
    r"<(?P<tag>environment_context|goal_context|objective|turn_aborted|codex_internal_context)\b[^>]*>\s*.*?</(?P=tag)>",
    re.DOTALL,
)

ORPHAN_GOAL_CONTEXT_RE = re.compile(
    r"</objective>\s*.*?</goal_context>",
    re.DOTALL,
)

AGENTS_INSTRUCTIONS_BLOCK_RE = re.compile(
    r"^# AGENTS\.md instructions for [^\n]*\n\s*<INSTRUCTIONS>\s*.*?</INSTRUCTIONS>\s*",
    re.DOTALL | re.MULTILINE,
)

INTERNAL_CONTEXT_STANDALONE_TAG_RE = re.compile(
    r"</?(?:environment_context|goal_context|objective|turn_aborted|codex_internal_context)\b[^>]*>",
    re.DOTALL,
)

SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChatRecord:
    role: str
    timestamp: str
    phase: str
    body: str


@dataclass
class MergedRecord:
    role: str
    phase: str
    start: str
    end: str
    bodies: list[str]


@dataclass(frozen=True)
class SessionInfo:
    path: Path
    session_id: str | None = None
    thread_name: str | None = None


@dataclass(frozen=True)
class TraceRecord:
    timestamp: str
    kind: str
    body: str


def codex_home_from_arg(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_boundary(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_ts(ts: str | None) -> str:
    dt = parse_ts(ts)
    if not dt:
        return ts or ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated at {limit} chars]"


def hits(text: str, keywords: Iterable[str]) -> int:
    low = text.lower()
    return sum(1 for keyword in keywords if keyword.lower() in low)


def is_orphan_heading(paragraph: str) -> bool:
    lines = [line for line in paragraph.split("\n") if line.strip()]
    if not lines:
        return True
    heading_lines = sum(
        1 for line in lines
        if re.match(r"#{1,4}\s", line) or len(line.strip()) < 50
    )
    return heading_lines == len(lines) and len(paragraph) < 200


def strip_internal_context_blocks(text: str) -> str:
    """Drop Codex/TUI context blocks that are not real chat content."""
    previous = None
    while previous != text:
        previous = text
        text = AGENTS_INSTRUCTIONS_BLOCK_RE.sub("", text)
        text = INTERNAL_CONTEXT_BLOCK_RE.sub("", text)
        text = ORPHAN_GOAL_CONTEXT_RE.sub("", text)
        text = INTERNAL_CONTEXT_STANDALONE_TAG_RE.sub("", text)
    return text.strip()


def normalize_preset(mode: str | None, preset: str | None) -> str:
    selected = preset or mode or "decisions"
    normalized = PRESET_ALIASES.get(selected)
    if not normalized:
        valid = ", ".join(PRESET_ALIASES)
        raise ValueError(f"unknown preset/mode {selected!r}; valid values: {valid}")
    if preset and mode:
        mode_normalized = PRESET_ALIASES.get(mode)
        preset_normalized = PRESET_ALIASES.get(preset)
        if mode_normalized != preset_normalized:
            raise ValueError(
                f"--preset {preset!r} conflicts with --mode {mode!r}; use only one"
            )
    return normalized


def read_session_meta(path: Path) -> tuple[str | None, str | None]:
    try:
        with path.open() as f:
            for line in f:
                obj = json.loads(line)
                if obj.get("type") != "session_meta":
                    continue
                payload = obj.get("payload") or {}
                return payload.get("id"), None
    except Exception:
        return None, None
    return None, None


def read_thread_name_from_index(codex_home: Path, session_id: str | None) -> str | None:
    if not session_id:
        return None
    index = codex_home / "session_index.jsonl"
    if not index.exists():
        return None
    latest_name = None
    try:
        with index.open() as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("id") == session_id and obj.get("thread_name"):
                    latest_name = obj.get("thread_name")
    except Exception:
        return None
    return latest_name


def resolve_session(value: str, codex_home: Path) -> SessionInfo:
    candidate_path = Path(value).expanduser()
    if candidate_path.exists():
        path = candidate_path.resolve()
        session_id, _ = read_session_meta(path)
        return SessionInfo(
            path=path,
            session_id=session_id,
            thread_name=read_thread_name_from_index(codex_home, session_id),
        )

    if "/" in value or value.endswith(".jsonl"):
        raise FileNotFoundError(f"session JSONL not found: {value}")

    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        raise FileNotFoundError(f"Codex sessions directory not found: {sessions_dir}")

    candidates = list(sessions_dir.rglob(f"*{value}*.jsonl"))
    exact: list[Path] = []
    for candidate in candidates:
        session_id, _ = read_session_meta(candidate)
        if session_id == value:
            exact.append(candidate)

    if exact:
        candidates = exact
    elif SESSION_ID_RE.match(value):
        # Fall back to a metadata scan in case a future filename stops including
        # the session id. This is slower but only runs when direct glob misses.
        for candidate in sessions_dir.rglob("*.jsonl"):
            session_id, _ = read_session_meta(candidate)
            if session_id == value:
                candidates.append(candidate)

    if not candidates:
        raise FileNotFoundError(
            f"no Codex session JSONL found for {value!r} under {sessions_dir}"
        )

    candidates = sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)
    path = candidates[0].resolve()
    session_id, _ = read_session_meta(path)
    return SessionInfo(
        path=path,
        session_id=session_id,
        thread_name=read_thread_name_from_index(codex_home, session_id),
    )


def sanitize_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "codex-chat"


def default_output_path(session: SessionInfo, preset: str) -> Path:
    stem = session.thread_name or session.session_id or session.path.stem
    suffix = ".jsonl" if preset == "raw-jsonl" else ".md"
    return Path(f"{sanitize_filename(stem)}-{preset}{suffix}")


def extract_text(payload: dict) -> str:
    texts = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("input_text", "output_text", "text"):
            text = item.get("text") or ""
            if text.strip():
                texts.append(text)
    return strip_internal_context_blocks("\n\n".join(texts))


def iter_visible_messages(path: Path, boundary: datetime | None = None) -> Iterator[ChatRecord]:
    with path.open() as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload") or {}
            if payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            ts_dt = parse_ts(obj.get("timestamp"))
            if ts_dt is None:
                continue
            if boundary and ts_dt <= boundary:
                continue
            text = extract_text(payload)
            if text.strip():
                yield ChatRecord(
                    role=role,
                    timestamp=fmt_ts(obj.get("timestamp")),
                    phase=payload.get("phase") or "",
                    body=text,
                )


def iter_jsonl_objects(path: Path, boundary: datetime | None = None) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if boundary:
                ts_dt = parse_ts(obj.get("timestamp"))
                if ts_dt is not None and ts_dt <= boundary:
                    continue
            yield obj


def jsonl_record_count(path: Path, boundary: datetime | None = None) -> int:
    return sum(1 for _ in iter_jsonl_objects(path, boundary))


def event_kind(obj: dict) -> str:
    top_type = obj.get("type") or "unknown"
    payload = obj.get("payload") or {}
    if not isinstance(payload, dict):
        return top_type

    payload_type = payload.get("type")
    if top_type == "response_item":
        if payload_type == "message":
            role = payload.get("role") or "unknown"
            phase = payload.get("phase")
            return f"response_item/message/{role}" + (f"/{phase}" if phase else "")
        name = payload.get("name")
        if name:
            return f"response_item/{payload_type}/{name}"
        return f"response_item/{payload_type or 'unknown'}"
    if top_type == "event_msg" and payload_type:
        return f"event_msg/{payload_type}"
    return f"{top_type}/{payload_type}" if payload_type else top_type


def event_json_ready(value):
    """Keep event exports readable without dumping opaque encrypted blobs."""
    if isinstance(value, dict):
        ready = {}
        for key, item in value.items():
            if key in {"encrypted_content", "developer_instructions"} and isinstance(item, str):
                ready[key] = f"<omitted {len(item)} chars>"
            else:
                ready[key] = event_json_ready(item)
        return ready
    if isinstance(value, list):
        return [event_json_ready(item) for item in value]
    return value


def build_trace_records(path: Path, boundary: datetime | None = None) -> list[TraceRecord]:
    records = []
    for obj in iter_jsonl_objects(path, boundary):
        records.append(
            TraceRecord(
                timestamp=fmt_ts(obj.get("timestamp")),
                kind=event_kind(obj),
                body=json.dumps(event_json_ready(obj), ensure_ascii=False, indent=2),
            )
        )
    return records


def render_trace_markdown(
    session: SessionInfo,
    records: Iterable[TraceRecord],
    since: str | None = None,
) -> str:
    lines = [
        "# Codex Session Trace",
        f"Preset: {PRESET_LABELS['trace']}",
        f"Source: {session.path}",
    ]
    if session.session_id:
        lines.append(f"Session: {session.session_id}")
    if session.thread_name:
        lines.append(f"Thread: {session.thread_name}")
    if since:
        lines.append(f"Window: after {since} UTC")
    lines.append("")

    for index, item in enumerate(records, start=1):
        ts_s = f" [{item.timestamp}]" if item.timestamp else ""
        lines.append(f"\n## Trace Record {index}{ts_s} `{item.kind}`")
        lines.append("```json")
        lines.append(item.body)
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def render_raw_jsonl(path: Path, boundary: datetime | None = None) -> str:
    if boundary is None:
        return path.read_text()

    lines = []
    with path.open() as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts_dt = parse_ts(obj.get("timestamp"))
            if ts_dt is not None and ts_dt <= boundary:
                continue
            lines.append(line if line.endswith("\n") else f"{line}\n")
    return "".join(lines)


def keep_user_body(text: str, preset: str) -> str:
    stripped = text.strip()
    if not stripped or any(drop in stripped for drop in SUBSTR_DROP):
        return ""
    if preset == "full":
        return truncate(stripped)

    first = stripped.split("\n", 1)[0]
    if len(stripped) < 80 and USER_DROP_RE.match(first):
        return ""
    if hits(stripped, ENV_KEYWORDS) >= 2 and len(stripped) < 400:
        return ""

    if preset == "decisions":
        if hits(stripped, UE_KEYWORDS) >= 1:
            return ""
        if len(stripped) < 40 and not ("?" in stripped or "？" in stripped):
            return ""

    return truncate(stripped)


def keep_assistant_body(text: str, preset: str) -> str:
    if any(drop in text for drop in SUBSTR_DROP):
        text = "\n".join(
            line for line in text.split("\n")
            if not any(drop in line for drop in SUBSTR_DROP)
        )
    if preset == "full":
        return text.strip()

    paragraphs = re.split(r"\n\s*\n", text)
    kept = []
    for paragraph in paragraphs:
        current = paragraph.strip()
        if not current:
            continue
        if hits(current, ENV_KEYWORDS) >= 2 and len(current) < 600:
            continue
        if preset == "decisions":
            if hits(current, UE_KEYWORDS) >= 1:
                continue
            if is_orphan_heading(current):
                continue
            has_code = "```" in current
            has_heading = bool(re.match(r"#{1,4}\s", current))
            has_verdict = bool(VERDICT_RE.search(current))
            long_structured = len(current) >= 600 and current.count("\n") >= 4
            if not (
                has_verdict or has_heading or long_structured
                or (has_code and len(current) >= 100)
            ):
                continue
        kept.append(current)
    return "\n\n".join(kept) if kept else ""


def build_records(path: Path, preset: str, boundary: datetime | None = None) -> list[ChatRecord]:
    records = []
    for record in iter_visible_messages(path, boundary):
        if record.role == "user":
            body = keep_user_body(record.body, preset)
        else:
            body = keep_assistant_body(record.body, preset)
        if body:
            records.append(ChatRecord(record.role, record.timestamp, record.phase, body))
    return records


def merge_records(records: Iterable[ChatRecord]) -> list[MergedRecord]:
    merged: list[MergedRecord] = []
    for record in records:
        if merged and merged[-1].role == record.role and merged[-1].phase == record.phase:
            merged[-1].bodies.append(record.body)
            merged[-1].end = record.timestamp
        else:
            merged.append(
                MergedRecord(
                    role=record.role,
                    phase=record.phase,
                    start=record.timestamp,
                    end=record.timestamp,
                    bodies=[record.body],
                )
            )
    return merged


def render_markdown(
    session: SessionInfo,
    preset: str,
    merged: Iterable[MergedRecord],
    since: str | None = None,
) -> str:
    lines = [
        f"# Codex Chat History ({preset})",
        f"Preset: {PRESET_LABELS[preset]}",
        f"Source: {session.path}",
    ]
    if session.session_id:
        lines.append(f"Session: {session.session_id}")
    if session.thread_name:
        lines.append(f"Thread: {session.thread_name}")
    if since:
        lines.append(f"Window: after {since} UTC")
    lines.append("")

    for item in merged:
        label = "User" if item.role == "user" else "Assistant"
        phase = f" [{item.phase}]" if item.phase else ""
        ts_s = item.start if item.start == item.end else f"{item.start} -> {item.end}"
        lines.append(f"\n## {label} [{ts_s}]{phase}")
        lines.append("\n\n".join(item.bodies))
    lines.append("")
    return "\n".join(lines)


def probe(session: SessionInfo, preset: str, boundary: datetime | None = None) -> None:
    if preset in {"trace", "raw-jsonl"}:
        objects = list(iter_jsonl_objects(session.path, boundary))
        records = [
            fmt_ts(obj.get("timestamp"))
            for obj in objects
            if obj.get("timestamp")
        ]
        first = records[0] if records else None
        last = records[-1] if records else None
        count = len(objects)
    else:
        visible_records = list(iter_visible_messages(session.path, boundary))
        first = visible_records[0].timestamp if visible_records else None
        last = visible_records[-1].timestamp if visible_records else None
        count = len(visible_records)
    print(f"path:    {session.path}")
    if session.session_id:
        print(f"session: {session.session_id}")
    if session.thread_name:
        print(f"thread:  {session.thread_name}")
    print(f"preset:  {preset}")
    print(f"records: {count}")
    print(f"first:   {first}")
    print(f"last:    {last}")


def build_arg_parser() -> argparse.ArgumentParser:
    description = """Export local Codex session JSONL files to Markdown.

Input can be either a JSONL path or a Codex session id. The default preset is
decisions, which is the old substantive mode under a clearer name.
"""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("session", nargs="?", help="Codex session JSONL path or session id")
    parser.add_argument(
        "output",
        nargs="?",
        help="Output .md path. Defaults to ./<thread-or-session>-<preset>.md",
    )
    parser.add_argument(
        "--preset",
        choices=[
            "full", "readable", "decisions", "trace", "raw-jsonl",
            "raw", "clean", "substantive", "jsonl",
        ],
        default=None,
        help=(
            "Export preset. Prefer full/readable/decisions/trace/raw-jsonl. "
            "raw/clean/substantive are legacy aliases."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=[
            "raw", "clean", "substantive", "full", "readable", "decisions",
            "trace", "raw-jsonl", "jsonl",
        ],
        default=None,
        help="Deprecated alias for --preset; kept for old commands.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "UTC ISO timestamp; only keep records strictly after this. "
            "Example: 2026-05-17T10:52:04"
        ),
    )
    parser.add_argument(
        "--codex-home",
        default=None,
        help="Codex home directory for session-id lookup. Defaults to $CODEX_HOME or ~/.codex.",
    )
    parser.add_argument("--probe", action="store_true", help="Print first/last timestamps and exit")
    parser.add_argument("--stdout", action="store_true", help="Write Markdown to stdout")
    parser.add_argument("--list-presets", action="store_true", help="Print preset names and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        for name in ("full", "readable", "decisions", "trace", "raw-jsonl"):
            print(f"{name}: {PRESET_LABELS[name]}")
        print("aliases: jsonl=raw-jsonl")
        print("legacy aliases: raw=full, clean=readable, substantive=decisions")
        return 0

    if not args.session:
        parser.error("session JSONL path or session id required")

    try:
        preset = normalize_preset(args.mode, args.preset)
        boundary = parse_boundary(args.since)
        codex_home = codex_home_from_arg(args.codex_home)
        session = resolve_session(args.session, codex_home)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.probe:
        probe(session, preset, boundary)
        return 0

    if preset == "raw-jsonl":
        output_text = render_raw_jsonl(session.path, boundary)
        record_count = jsonl_record_count(session.path, boundary)
        section_count = record_count
    elif preset == "trace":
        trace_records = build_trace_records(session.path, boundary)
        output_text = render_trace_markdown(session, trace_records, args.since)
        record_count = len(trace_records)
        section_count = len(trace_records)
    else:
        records = build_records(session.path, preset, boundary)
        merged = merge_records(records)
        output_text = render_markdown(session, preset, merged, args.since)
        record_count = len(records)
        section_count = len(merged)

    if args.stdout:
        sys.stdout.write(output_text)
    else:
        output = Path(args.output).expanduser() if args.output else default_output_path(session, preset)
        output.write_text(output_text)
        print(f"wrote {output}")
    print(f"preset: {preset}", file=sys.stderr if args.stdout else sys.stdout)
    print(f"records: {record_count}; merged sections: {section_count}", file=sys.stderr if args.stdout else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
