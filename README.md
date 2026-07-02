# Codex Chat Exporter

Export local Codex session JSONL files to readable Markdown.

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
codex-chat-export 019e36b3-4357-7381-be9a-2b6b52cd9639 --probe
codex-chat-export 019e36b3-4357-7381-be9a-2b6b52cd9639 out.md --preset full
codex-chat-export /home/chris/.codex/sessions/2026/05/17/rollout-xxx.jsonl out.md --preset readable
```

Run through `uvx` from GitHub:

```bash
uvx --from "codex-chat-exporter @ git+https://github.com/dqtz5vpvj9-create/codex-chat-exporter.git" codex-chat-export 019e36b3-4357-7381-be9a-2b6b52cd9639 out.md --preset decisions
```

Run through `uvx` from a local checkout:

```bash
uvx --from /home/chris/codex-chat-exporter codex-chat-export 019e36b3-4357-7381-be9a-2b6b52cd9639 out.md --preset decisions
```

## Presets

- `full`: complete visible user/assistant chat, minus internal Codex context tag blocks.
- `readable`: drops obvious environment noise and short tactical messages.
- `decisions`: compact extract focused on verdicts, conclusions, and evidence-heavy paragraphs.

Legacy names still work:

- `raw` -> `full`
- `clean` -> `readable`
- `substantive` -> `decisions`
