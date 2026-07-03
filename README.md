# Codex Chat Exporter

Turn local Codex session JSONL files into readable Markdown.

`codex-chat-exporter` is a tiny, dependency-free CLI for extracting the human
conversation from Codex logs. It keeps the useful user/assistant trail, removes
Codex internal context blocks, and offers compact presets for long sessions that
would otherwise become unreadable walls of tool output.

## Why It Exists

Codex session files are excellent for recovery and audit, but raw JSONL is hard
to read. Large sessions often contain tool calls, internal context tags,
developer messages, repeated environment metadata, and huge command outputs.

This tool focuses on the part people usually want to read or share:

- the actual user and assistant conversation
- a clean Markdown document
- fast exports from either a session id or a direct JSONL path
- presets that range from full transcript to compact decision log

## Highlights

- **Fast on large logs**: exports a 112MB / 54,314-row session in about 1.2-1.4s.
- **Five useful presets**: `full`, `readable`, `decisions`, `events`, and `raw-jsonl`.
- **No runtime dependencies**: Python standard library only.
- **Session id lookup**: pass a Codex session id and it finds the matching JSONL under `~/.codex/sessions`.
- **Direct file mode**: pass any `rollout-*.jsonl` path.
- **Markdown-first output**: easy to read, diff, archive, email, or feed into another model.

## Quick Start

Run directly from GitHub with `uvx`:

```bash
uvx --from "codex-chat-exporter @ git+https://github.com/dqtz5vpvj9-create/codex-chat-exporter.git" \
  codex-chat-export SESSION_ID out.md --preset decisions
```

Export by direct JSONL path:

```bash
uvx --from "codex-chat-exporter @ git+https://github.com/dqtz5vpvj9-create/codex-chat-exporter.git" \
  codex-chat-export ~/.codex/sessions/2026/05/17/rollout-xxx.jsonl out.md --preset readable
```

Run from a local checkout:

```bash
git clone https://github.com/dqtz5vpvj9-create/codex-chat-exporter.git
cd codex-chat-exporter
python -m pip install .
codex-chat-export SESSION_ID out.md --preset full
```

## Presets

| Preset | Best for | What it keeps |
| --- | --- | --- |
| `full` | high-fidelity chat archive | complete visible user/assistant transcript, with internal Codex context tags removed; excludes tool calls, tool outputs, reasoning records, and event messages |
| `readable` | day-to-day reading | full chat with obvious noise and short tactical messages removed |
| `decisions` | quick review, handoff, summaries | compact conclusion/evidence-focused extract |
| `events` | debugging exporter behavior or agent execution | all JSONL events rendered as Markdown, including tool calls and tool outputs; bulky encrypted/internal blobs are omitted |
| `raw-jsonl` | exact archival or downstream parsing | exact original session JSONL, including tool calls, tool outputs, reasoning records, event messages, and internal records |

Legacy names still work:

| Legacy | Current |
| --- | --- |
| `raw` | `full` for backward compatibility; use `raw-jsonl` for exact raw JSONL |
| `clean` | `readable` |
| `substantive` | `decisions` |

## Benchmark

Same 112MB Codex session JSONL, 54,314 rows, benchmarked on Linux CLI.

| 工具/模式 | 耗时 | 体积 | 行数 |
| --- | ---: | ---: | ---: |
| codex-chat-exporter (full) | 1.239s | 941.0KB | 15,546 |
| codex-chat-exporter (readable) | 1.320s | 916.3KB | 15,144 |
| codex-chat-exporter (decisions) | 1.405s | 171.5KB | 3,061 |
| MeXenon/codex-session-export (Chat Clean) | 1.362s | 989.4KB | 22,776 |
| tobitege/codlogs (--md default) | 5.763s | 1.1MB | 23,239 |
| timvw/codex-transcripts (html archive) | 8.064s | 778.3KB index / 73.5MB archive | 15,759 index / 42 pages |
| brucehart/codex-transcripts (md+html archive) | 31.308s | 55.2MB transcript / 72.5MB archive | 771,286 |
| nicosuave/memex session dump (pre-indexed) | 2.594s | 223.8MB | 74,541 |

## Usage

Use either a session JSONL path or a Codex session id:

```bash
codex-chat-export SESSION_ID --probe
codex-chat-export SESSION_ID out.md --preset full
codex-chat-export ~/.codex/sessions/2026/05/17/rollout-xxx.jsonl out.md --preset readable
codex-chat-export ~/.codex/sessions/2026/05/17/rollout-xxx.jsonl out.md --preset decisions
codex-chat-export SESSION_ID events.md --preset events
codex-chat-export SESSION_ID raw.jsonl --preset raw-jsonl
```

Print to stdout:

```bash
codex-chat-export SESSION_ID --preset decisions --stdout
```

Export only records after a timestamp:

```bash
codex-chat-export SESSION_ID out.md \
  --preset readable \
  --since 2026-05-17T10:52:04
```

Use a custom Codex home:

```bash
codex-chat-export SESSION_ID out.md \
  --codex-home /path/to/.codex
```

List presets:

```bash
codex-chat-export --list-presets
```

## Output Shape

The Markdown starts with session metadata, then emits merged chat sections:

```markdown
# Codex Chat History (decisions)
Preset: decisions (legacy substantive): compact conclusion/evidence extract
Source: ~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-<session-id>.jsonl
Session: <session-id>
Thread: <thread-title>

## User [2026-05-17 10:52:04]
Please summarize what happened in this Codex session.

## Assistant [2026-05-17 10:52:12] [final_answer]
The session fixed the failing export path, verified the output, and left a
clean Markdown transcript for review.
```

## Development

```bash
git clone https://github.com/dqtz5vpvj9-create/codex-chat-exporter.git
cd codex-chat-exporter
python -m pip install -e .
codex-chat-export --help
```

Run the module directly during development:

```bash
PYTHONPATH=src python -m codex_chat_exporter.cli --help
```

## License

MIT
