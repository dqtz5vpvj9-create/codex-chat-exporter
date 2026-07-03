#!/usr/bin/env python3
"""Export local Codex session JSONL files to readable Markdown."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    "trace": "execution trace: readable Markdown for messages, tool calls, and tool outputs",
    "raw-jsonl": "raw JSONL: exact original session JSONL, including tool calls and internal records",
}

TRACE_TEXT_LIMIT = 20000
DEFAULT_TIMEZONE = "+08:00"
TEXT_ENCODING = "utf-8"

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
    title: str
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


def parse_timezone(value: str | None) -> tzinfo:
    value = (value or DEFAULT_TIMEZONE).strip()
    aliases = {
        "z": "UTC",
        "utc": "UTC",
        "gmt": "UTC",
        "东八区": "+08:00",
        "北京时间": "+08:00",
        "beijing": "+08:00",
        "china": "+08:00",
        "east8": "+08:00",
        "east-8": "+08:00",
    }
    normalized = aliases.get(value.lower(), value)
    if normalized == "UTC":
        return timezone.utc

    offset_match = re.fullmatch(
        r"(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?",
        normalized,
        re.IGNORECASE,
    )
    if offset_match:
        sign_s, hours_s, minutes_s = offset_match.groups()
        hours = int(hours_s)
        minutes = int(minutes_s or "0")
        if hours > 23 or minutes > 59:
            raise ValueError(f"invalid timezone offset: {value!r}")
        delta = timedelta(hours=hours, minutes=minutes)
        if sign_s == "-":
            delta = -delta
        label = f"UTC{sign_s}{hours:02d}:{minutes:02d}"
        return timezone(delta, label)

    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"unknown timezone {value!r}; use +08:00, UTC, America/Los_Angeles, Asia/Shanghai, etc."
        ) from exc


def timezone_label(display_tz: tzinfo) -> str:
    key = getattr(display_tz, "key", None)
    if key:
        return key
    name = display_tz.tzname(None)
    if name:
        return name
    offset = display_tz.utcoffset(None)
    if offset is None:
        return str(display_tz)
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def parse_boundary(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_ts(ts: str | None, display_tz: tzinfo) -> str:
    dt = parse_ts(ts)
    if not dt:
        return ts or ""
    return dt.astimezone(display_tz).strftime("%Y-%m-%d %H:%M:%S")


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
        with path.open(encoding=TEXT_ENCODING) as f:
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
        with index.open(encoding=TEXT_ENCODING) as f:
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


def iter_visible_messages(
    path: Path,
    boundary: datetime | None = None,
    display_tz: tzinfo = parse_timezone(None),
) -> Iterator[ChatRecord]:
    with path.open(encoding=TEXT_ENCODING) as f:
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
                    timestamp=fmt_ts(obj.get("timestamp"), display_tz),
                    phase=payload.get("phase") or "",
                    body=text,
                )


def iter_jsonl_objects(path: Path, boundary: datetime | None = None) -> Iterator[dict]:
    with path.open(encoding=TEXT_ENCODING) as f:
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


def trace_text(text, limit: int = TRACE_TEXT_LIMIT) -> str:
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False, indent=2)
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [trace output truncated at {limit} chars; use raw-jsonl for exact record]"


def fence(label: str, text, language: str = "text", limit: int = TRACE_TEXT_LIMIT) -> str:
    rendered = trace_text(text, limit)
    if not rendered.strip():
        return ""
    return f"{label}\n\n```{language}\n{rendered}\n```"


def json_fence(label: str, value, limit: int = TRACE_TEXT_LIMIT) -> str:
    return fence(label, json.dumps(value, ensure_ascii=False, indent=2), "json", limit)


def kv_lines(items: Iterable[tuple[str, object]]) -> list[str]:
    lines = []
    for key, value in items:
        if value is None or value == "":
            continue
        lines.append(f"- {key}: `{value}`")
    return lines


def decode_arguments(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def render_tool_arguments(name: str, args) -> str:
    if args is None:
        return ""
    if name == "exec_command" and isinstance(args, dict):
        lines = kv_lines(
            [
                ("workdir", args.get("workdir")),
                ("yield_time_ms", args.get("yield_time_ms")),
                ("max_output_tokens", args.get("max_output_tokens")),
            ]
        )
        cmd = args.get("cmd")
        if cmd:
            lines.append("")
            lines.append(fence("Command:", str(cmd), "bash", TRACE_TEXT_LIMIT))
        rest = {
            key: value
            for key, value in args.items()
            if key not in {"cmd", "workdir", "yield_time_ms", "max_output_tokens"}
        }
        if rest:
            lines.append("")
            lines.append(json_fence("Other arguments:", rest))
        return "\n".join(lines).strip()

    if isinstance(args, dict):
        return json_fence("Arguments:", args)
    return fence("Arguments:", str(args), "text")


def render_message_payload(payload: dict) -> str:
    role = payload.get("role") or "message"
    if role == "developer":
        return "_Developer/internal instructions omitted; use `raw-jsonl` for exact content._"
    text = extract_text(payload)
    if text:
        return trace_text(text)
    return "_Internal context omitted._"


def render_trace_record(
    obj: dict,
    call_names: dict[str, str],
    display_tz: tzinfo,
) -> TraceRecord | None:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    top_type = obj.get("type") or "unknown"
    payload_type = payload.get("type")
    ts = fmt_ts(obj.get("timestamp"), display_tz)

    if top_type == "response_item" and payload_type == "message":
        role = payload.get("role") or "message"
        phase = payload.get("phase")
        title = f"Message: {role}" + (f" [{phase}]" if phase else "")
        return TraceRecord(ts, title, render_message_payload(payload))

    if top_type == "response_item" and payload_type == "function_call":
        name = payload.get("name") or "tool"
        call_id = payload.get("call_id")
        if call_id:
            call_names[call_id] = name
        lines = kv_lines([("call_id", call_id)])
        args_body = render_tool_arguments(name, decode_arguments(payload.get("arguments")))
        if args_body:
            if lines:
                lines.append("")
            lines.append(args_body)
        return TraceRecord(ts, f"Tool Call: {name}", "\n".join(lines).strip())

    if top_type == "response_item" and payload_type == "function_call_output":
        call_id = payload.get("call_id")
        name = call_names.get(call_id, "tool")
        lines = kv_lines([("call_id", call_id)])
        output = payload.get("output") or ""
        if output:
            if lines:
                lines.append("")
            lines.append(fence("Output:", output, "text", TRACE_TEXT_LIMIT))
        return TraceRecord(ts, f"Tool Output: {name}", "\n".join(lines).strip())

    if top_type == "response_item" and payload_type == "custom_tool_call":
        name = payload.get("name") or "custom_tool"
        call_id = payload.get("call_id")
        if call_id:
            call_names[call_id] = name
        lines = kv_lines([("call_id", call_id), ("status", payload.get("status"))])
        tool_input = payload.get("input") or ""
        if tool_input:
            if lines:
                lines.append("")
            language = "diff" if name == "apply_patch" else "text"
            lines.append(fence("Input:", tool_input, language, TRACE_TEXT_LIMIT))
        return TraceRecord(ts, f"Custom Tool Call: {name}", "\n".join(lines).strip())

    if top_type == "response_item" and payload_type == "custom_tool_call_output":
        call_id = payload.get("call_id")
        name = call_names.get(call_id, "custom_tool")
        lines = kv_lines([("call_id", call_id)])
        output = payload.get("output") or ""
        if output:
            if lines:
                lines.append("")
            lines.append(fence("Output:", output, "text", TRACE_TEXT_LIMIT))
        return TraceRecord(ts, f"Custom Tool Output: {name}", "\n".join(lines).strip())

    if top_type == "response_item" and payload_type == "reasoning":
        summary = payload.get("summary") or []
        if summary:
            return TraceRecord(ts, "Reasoning Summary", json_fence("Summary:", summary))
        return None

    if top_type == "response_item" and payload_type == "web_search_call":
        return TraceRecord(
            ts,
            "Web Search",
            "\n".join(kv_lines([("status", payload.get("status")), ("action", payload.get("action"))])),
        )

    if top_type == "event_msg":
        if payload_type in {"agent_message", "user_message"}:
            return None
        if payload_type == "task_started":
            return TraceRecord(
                ts,
                "Task Started",
                "\n".join(
                    kv_lines(
                        [
                            ("turn_id", payload.get("turn_id")),
                            ("model_context_window", payload.get("model_context_window")),
                            ("collaboration_mode", payload.get("collaboration_mode_kind")),
                        ]
                    )
                ),
            )
        if payload_type == "task_complete":
            return TraceRecord(
                ts,
                "Task Complete",
                "\n".join(
                    kv_lines(
                        [
                            ("turn_id", payload.get("turn_id")),
                            ("duration_ms", payload.get("duration_ms")),
                            ("time_to_first_token_ms", payload.get("time_to_first_token_ms")),
                        ]
                    )
                ),
            )
        if payload_type == "token_count":
            return None
        if payload_type == "patch_apply_end":
            lines = kv_lines(
                [
                    ("call_id", payload.get("call_id")),
                    ("success", payload.get("success")),
                    ("status", payload.get("status")),
                ]
            )
            for key in ("stdout", "stderr"):
                value = payload.get(key)
                if value:
                    if lines:
                        lines.append("")
                    lines.append(fence(f"{key}:", value, "text", 4000))
            changes = payload.get("changes")
            if changes:
                if lines:
                    lines.append("")
                lines.append(json_fence("Changes:", changes, 4000))
            return TraceRecord(ts, "Patch Apply Result", "\n".join(lines).strip())
        if payload_type in {"turn_aborted", "context_compacted", "thread_rolled_back", "thread_goal_updated", "web_search_end"}:
            details = {key: value for key, value in payload.items() if key != "type"}
            return TraceRecord(ts, f"Event: {payload_type}", json_fence("Details:", details, 4000))
        return TraceRecord(ts, f"Event: {payload_type or 'unknown'}", json_fence("Payload:", payload, 4000))

    if top_type == "session_meta":
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        return TraceRecord(
            ts,
            "Session Metadata",
            "\n".join(
                kv_lines(
                    [
                        ("id", payload.get("id")),
                        ("session_id", payload.get("session_id")),
                        ("cwd", payload.get("cwd")),
                        ("cli_version", payload.get("cli_version")),
                        ("source", payload.get("source")),
                    ]
                )
            ),
        )

    if top_type == "turn_context":
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        return TraceRecord(
            ts,
            "Turn Context",
            "\n".join(
                kv_lines(
                    [
                        ("turn_id", payload.get("turn_id")),
                        ("cwd", payload.get("cwd")),
                        ("model", payload.get("model")),
                        ("current_date", payload.get("current_date")),
                        ("timezone", payload.get("timezone")),
                        ("approval_policy", payload.get("approval_policy")),
                    ]
                )
            ),
        )

    if top_type == "compacted":
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        message = payload.get("message") or "_Context compacted._"
        return TraceRecord(ts, "Context Compacted", trace_text(message, 4000))

    return TraceRecord(ts, event_kind(obj), json_fence("Record:", obj, 4000))


def build_trace_records(
    path: Path,
    boundary: datetime | None = None,
    display_tz: tzinfo = parse_timezone(None),
) -> list[TraceRecord]:
    records = []
    call_names: dict[str, str] = {}
    for obj in iter_jsonl_objects(path, boundary):
        record = render_trace_record(obj, call_names, display_tz)
        if record is not None:
            records.append(record)
    return records


def render_trace_markdown(
    session: SessionInfo,
    records: Iterable[TraceRecord],
    display_tz: tzinfo,
    since: str | None = None,
) -> str:
    lines = [
        "# Codex Session Trace",
        f"Preset: {PRESET_LABELS['trace']}",
        f"Source: {session.path}",
        f"Timezone: {timezone_label(display_tz)}",
    ]
    if session.session_id:
        lines.append(f"Session: {session.session_id}")
    if session.thread_name:
        lines.append(f"Thread: {session.thread_name}")
    if since:
        lines.append(f"Window: after {since}")
    lines.append("")

    for index, item in enumerate(records, start=1):
        ts_s = f" [{item.timestamp}]" if item.timestamp else ""
        lines.append(f"\n## Trace Record {index}{ts_s} - {item.title}")
        if item.body:
            lines.append(item.body)
    lines.append("")
    return "\n".join(lines)


def render_raw_jsonl(path: Path, boundary: datetime | None = None) -> str:
    if boundary is None:
        return path.read_text(encoding=TEXT_ENCODING)

    lines = []
    with path.open(encoding=TEXT_ENCODING) as f:
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


def build_records(
    path: Path,
    preset: str,
    boundary: datetime | None = None,
    display_tz: tzinfo = parse_timezone(None),
) -> list[ChatRecord]:
    records = []
    for record in iter_visible_messages(path, boundary, display_tz):
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
    display_tz: tzinfo,
    since: str | None = None,
) -> str:
    lines = [
        f"# Codex Chat History ({preset})",
        f"Preset: {PRESET_LABELS[preset]}",
        f"Source: {session.path}",
        f"Timezone: {timezone_label(display_tz)}",
    ]
    if session.session_id:
        lines.append(f"Session: {session.session_id}")
    if session.thread_name:
        lines.append(f"Thread: {session.thread_name}")
    if since:
        lines.append(f"Window: after {since}")
    lines.append("")

    for item in merged:
        label = "User" if item.role == "user" else "Assistant"
        phase = f" [{item.phase}]" if item.phase else ""
        ts_s = item.start if item.start == item.end else f"{item.start} -> {item.end}"
        lines.append(f"\n## {label} [{ts_s}]{phase}")
        lines.append("\n\n".join(item.bodies))
    lines.append("")
    return "\n".join(lines)


def probe(
    session: SessionInfo,
    preset: str,
    boundary: datetime | None = None,
    display_tz: tzinfo = parse_timezone(None),
) -> None:
    if preset == "trace":
        trace_records = build_trace_records(session.path, boundary, display_tz)
        records = [record.timestamp for record in trace_records if record.timestamp]
        first = records[0] if records else None
        last = records[-1] if records else None
        count = len(trace_records)
    elif preset == "raw-jsonl":
        objects = list(iter_jsonl_objects(session.path, boundary))
        records = [
            fmt_ts(obj.get("timestamp"), display_tz)
            for obj in objects
            if obj.get("timestamp")
        ]
        first = records[0] if records else None
        last = records[-1] if records else None
        count = len(objects)
    else:
        visible_records = list(iter_visible_messages(session.path, boundary, display_tz))
        first = visible_records[0].timestamp if visible_records else None
        last = visible_records[-1].timestamp if visible_records else None
        count = len(visible_records)
    print(f"path:    {session.path}")
    if session.session_id:
        print(f"session: {session.session_id}")
    if session.thread_name:
        print(f"thread:  {session.thread_name}")
    print(f"preset:  {preset}")
    print(f"timezone: {timezone_label(display_tz)}")
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
            "ISO timestamp; only keep records strictly after this. "
            "Naive timestamps are treated as UTC for backward compatibility. "
            "Example: 2026-05-17T10:52:04 or 2026-05-17T18:52:04+08:00"
        ),
    )
    parser.add_argument(
        "--timezone",
        "--tz",
        default=DEFAULT_TIMEZONE,
        help=(
            "Display timezone for exported Markdown and --probe. "
            "Default: +08:00. Examples: +08:00, UTC, America/Los_Angeles, Asia/Shanghai"
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


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding=TEXT_ENCODING)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
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
        display_tz = parse_timezone(args.timezone)
        codex_home = codex_home_from_arg(args.codex_home)
        session = resolve_session(args.session, codex_home)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.probe:
        probe(session, preset, boundary, display_tz)
        return 0

    if preset == "raw-jsonl":
        output_text = render_raw_jsonl(session.path, boundary)
        record_count = jsonl_record_count(session.path, boundary)
        section_count = record_count
    elif preset == "trace":
        trace_records = build_trace_records(session.path, boundary, display_tz)
        output_text = render_trace_markdown(session, trace_records, display_tz, args.since)
        record_count = len(trace_records)
        section_count = len(trace_records)
    else:
        records = build_records(session.path, preset, boundary, display_tz)
        merged = merge_records(records)
        output_text = render_markdown(session, preset, merged, display_tz, args.since)
        record_count = len(records)
        section_count = len(merged)

    if args.stdout:
        sys.stdout.write(output_text)
    else:
        output = Path(args.output).expanduser() if args.output else default_output_path(session, preset)
        with output.open("w", encoding=TEXT_ENCODING, newline="\n") as f:
            f.write(output_text)
        print(f"wrote {output}")
    print(f"preset: {preset}", file=sys.stderr if args.stdout else sys.stdout)
    print(f"records: {record_count}; merged sections: {section_count}", file=sys.stderr if args.stdout else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
