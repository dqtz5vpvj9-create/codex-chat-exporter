# Codex Chat Exporter

Export local Codex session JSONL files to readable Markdown.

## Usage

Use either a session JSONL path or a Codex session id:

```bash
codex-chat-export 019e36b3-4357-7381-be9a-2b6b52cd9639 --probe
codex-chat-export 019e36b3-4357-7381-be9a-2b6b52cd9639 out.md --preset full
codex-chat-export /home/chris/.codex/sessions/2026/05/17/rollout-xxx.jsonl out.md --preset readable
```

Run through `uvx` from GitHub:

```bash
uvx --from git+https://github.com/dqtz5vpvj9-create/codex-chat-exporter.git codex-chat-export 019e36b3-4357-7381-be9a-2b6b52cd9639 out.md --preset decisions
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
