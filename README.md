# claude-code-hooks — Hooks for developing safely with Claude Code

[日本語版: README.ja.md](README.ja.md)

A public collection of 8 Claude Code Hooks that guard against agent mistakes and runaway behavior, designed with community best practices in mind ([sources here](docs/best-practices.md)).

> Note: `docs/` is authoritative in Japanese; English translations of the per-hook reference, configuration guide, security model, and best-practices document are a future work item (see the design spec, section 6). This README is the primary bilingual entry point.

## 1. Overview — what this prevents

This Hooks collection targets five categories of risk that can occur while developing with Claude Code:

1. **Destructive command execution** — `rm -rf /`, `sudo rm`, force-pushing to protected branches, `mkfs`, `dd`, fork bombs, `DROP TABLE`, etc.
2. **Leakage of sensitive information** — reading/editing/accessing secret files such as `.env` or private keys, or writing/exfiltrating secrets
3. **Quality degradation** — edits slipping in without lint/format checks
4. **Lack of visibility** — no record of what ran, or when a permission prompt occurred
5. **Leakage via MCP tool input/output** — credentials, PII, or confidential business information sent or received through MCP/Web tools

None of this defends against a malicious user — it exists to **prevent agent mistakes and runaway behavior** (see [Guarantees](#6-guarantees) below and [docs/security-model.md](docs/security-model.md), which is written in Japanese).

## 2. Quickstart

### Prerequisites

- [`uv`](https://docs.astral.sh/uv/) is required (uv resolves the Python runtime itself, no separate install needed)
- `exfil_guard`'s semantic judgment (LLM-based DLP detection) only runs when the Claude Code CLI (`claude`) is on `PATH`. If it isn't found, semantic judgment is skipped automatically and the other regex-based categories keep working

### Install as a plugin

```
/plugin marketplace add wwwcojp/claude-code-hooks
/plugin install safe-dev-hooks
```

This enables all 8 hooks exactly as wired in `hooks/hooks.json`.

### Manual install (copy-paste partial adoption is fine too)

```bash
git clone https://github.com/wwwcojp/claude-code-hooks.git
```

Merge the contents of [`examples/settings.full.json`](examples/settings.full.json) (all hooks) or [`examples/settings.minimal.json`](examples/settings.minimal.json) (`bash_guard` + `secrets_guard` only) into `~/.claude/settings.json`. Replace `$HOME/claude-code-hooks` in the paths with wherever you actually cloned the repo. Because the architecture is one-concern-per-module, you can adopt only the hooks you need.

## 3. Hook list

| Hook | Event / matcher | Behavior |
|------|--------------------|------|
| [bash_guard](docs/hooks/bash_guard.md) | PreToolUse / `Bash` | Irrecoverable operations (`rm -rf /`, `sudo rm`, force-push to a protected branch, `mkfs`, `dd`, fork bombs, `DROP TABLE`, etc.) are denied. Gray-area operations (`git reset --hard`, `git clean -f`, recursive/forced `rm`, `curl\|bash`, etc.) trigger ask. Commands chained with `&&` `;` `\|\|` are split and each segment is inspected |
| [secrets_guard](docs/hooks/secrets_guard.md) | PreToolUse / `Read\|Edit\|Write\|Bash` | Denies reading/editing/catting `.env` (`.env.example` etc. are allowed), `*.pem` / `id_rsa`, `~/.ssh/`, `~/.aws/credentials`, and similar |
| [exfil_guard](docs/hooks/exfil_guard.md) | PreToolUse / `mcp__.*\|WebFetch\|WebSearch` | DLP inspection of outbound arguments (credentials, PII, confidentiality markers, custom patterns, semantic judgment) |
| [exfil_output_scan](docs/hooks/exfil_output_scan.md) | PostToolUse / `mcp__.*\|WebFetch\|WebSearch` | Detects secrets/PII in responses. Configurable to warn (`additionalContext`) or mask (`updatedToolOutput`) |
| [quality_gate](docs/hooks/quality_gate.md) | PostToolUse / `Edit\|Write` | Runs lint/format on the edited file; failures use `decision:block` to make Claude self-correct (warn/block mode configurable) |
| [secrets_scan](docs/hooks/secrets_scan.md) | PostToolUse / `Edit\|Write` | Detects AWS keys, GitHub tokens, private-key blocks, etc. in written content and blocks |
| [audit_log](docs/hooks/audit_log.md) | PreToolUse / PostToolUse / SessionStart / SessionEnd / Stop / `*` | Asynchronously records every tool call and session boundary as JSONL |
| [notify](docs/hooks/notify.md) | Notification | Notifies on permission-wait/idle (default: terminal bell, command replaceable) |

## 4. Configuration

All keys are optional. Every guard runs with safe defaults even with no configuration file at all. The project's `.claude-hooks.json` takes highest priority, then `~/.claude/claude-hooks.json` (personal defaults), then the bundled `rules/*.json` (built-in defaults) — all three layers are merged.

Minimal example:

```json
{
  "bash_guard": {
    "extra_deny": ["docker system prune"]
  },
  "exfil_guard": {
    "trusted_servers": ["mcp__internal-kb"]
  }
}
```

See [docs/configuration.md](docs/configuration.md) (Japanese) for the full schema, the 3-layer merge details, and personal/team/high-security configuration presets.

## 5. Verifying it works (required first-run warmup)

Each hook script is a `uv run --script` shebang, so its very first invocation on a machine may need to fetch/install a Python interpreter, which can take longer than the hooks' own 10-second timeout. Run the command below once, right after installation, so `uv` finishes that setup outside of an actual hook invocation — treat it as a mandatory warmup step, not just an optional sanity check.

You can confirm `bash_guard` denies a destructive command by piping a mock event straight into the hook script:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | uv run hooks/pre_tool_use/bash_guard.py
```

This is working correctly if it returns a single line of JSON containing `permissionDecision: "deny"`:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "破壊的コマンドを検出: rm-root-or-home(deny層は設定で解除できません)"
  }
}
```

(The `permissionDecisionReason` text is in Japanese, matching the hook's own messages.)

## 6. Guarantees

Hooks can be disabled via Claude Code's `disableAllHooks` setting or by removing the hook configuration itself, so this project is **not a defense against a malicious user — it's a mechanism to prevent agent mistakes and runaway behavior**. The deny-tier patterns block deterministically regardless of permission mode and cannot be lifted from the configuration file, but there are known limits to regex coverage and to the probabilistic nature of semantic judgment. See [docs/security-model.md](docs/security-model.md) (Japanese) for the full picture of what is and isn't guaranteed.

## 7. License / Contributing

- License: [LICENSE](LICENSE) (MIT)
- How to contribute: [CONTRIBUTING.md](CONTRIBUTING.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
