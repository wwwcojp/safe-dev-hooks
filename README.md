# safe-dev-hooks — develop safely with Claude Code

[日本語版: README.ja.md](README.ja.md)

A collection of 8 [Claude Code Hooks](https://docs.claude.com/en/docs/claude-code/hooks) that catch an AI agent's mistakes and runaway actions before they happen — destructive commands, secret leaks, and unreviewed edits — with safe defaults that work out of the box and no configuration required. Designed around community [best practices](docs/best-practices.md).

## What it protects against

While you develop with Claude Code, these hooks guard against five kinds of accident:

1. **Destructive commands** — `rm -rf /`, `sudo rm`, force-pushing a protected branch, `mkfs`, `dd`, fork bombs, `DROP TABLE`, and the like.
2. **Secret leaks** — reading, editing, or exfiltrating `.env` files, private keys, and cloud credentials.
3. **Quality slipping** — edits landing without a lint/format check.
4. **No paper trail** — no record of what ran or when a permission prompt appeared.
5. **Leaks through MCP/Web tools** — credentials, PII, or confidential business data flowing out through tool arguments or coming back in tool responses.

This is **not a defense against a malicious user** — anyone who can edit Claude Code's settings can turn the hooks off. It exists to stop the agent itself from making expensive mistakes. See [Guarantees](#guarantees--and-limits) for exactly what that does and doesn't cover.

## Install

### 1. Add the plugin

```
/plugin marketplace add wwwcojp/safe-dev-hooks
/plugin install safe-dev-hooks
```

That enables all 8 hooks as wired in `hooks/hooks.json`. The only requirement is [`uv`](https://docs.astral.sh/uv/) on your `PATH` — it provisions the Python runtime itself, so you don't need a separate Python install. To run only a subset of the hooks, use [manual install](#manual-install--partial-adoption) instead.

### 2. Verify — required, once, right after installing

> [!IMPORTANT]
> **The hooks fail *open*.** If `uv` is missing or errors out, each hook exits non-zero, Claude Code only warns, and **every guard silently becomes a no-op**. You will not notice this during normal use — so run the check below once after installing (it also warms up `uv`'s first-run interpreter download, which can exceed a hook's 10-second timeout and fail open the same way).

Pipe a mock event straight into `bash_guard` and confirm it denies a destructive command:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | uv run hooks/pre_tool_use/bash_guard.py
```

It works if you get one line of JSON containing `"permissionDecision": "deny"`:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "破壊的コマンドを検出: rm-root-or-home(deny層は設定で解除できません)"}}
```

(Reason messages are in Japanese, matching the hooks' own output.)

### Manual install & partial adoption

Prefer to pick only some hooks, or not use the plugin system? Clone the repo and merge a settings snippet into `~/.claude/settings.json`:

```bash
git clone https://github.com/wwwcojp/safe-dev-hooks.git
```

- **All hooks:** merge [`examples/settings.full.json`](examples/settings.full.json).
- **Minimal (`bash_guard` + `secrets_guard` only):** merge [`examples/settings.minimal.json`](examples/settings.minimal.json).

In the snippet you merge, replace `$HOME/safe-dev-hooks` with your clone path. Each hook is one self-contained module, so adopting a subset is fine.

**Without `uv`:** the hook scripts use only the Python standard library, so they also run under any Python ≥ 3.10. Replace `uv run` with `python3` in the commands you merge — but then every hook fails (fail-open) if the system `python3` is older than 3.10, so re-run the verify step above. This route is manual-install only; the plugin is wired to `uv`.

> `exfil_guard`'s optional semantic (LLM-based) DLP check runs only when the `claude` CLI is on `PATH`. If it isn't, that one check is skipped and the regex-based checks keep working.

## The hooks

| Hook | Event / matcher | What it does |
|------|-----------------|--------------|
| [bash_guard](docs/hooks/bash_guard.md) | PreToolUse / `Bash` | **Denies** irrecoverable operations (`rm -rf /`, `sudo rm`, force-push to a protected branch — including `+refspec` forms, `mkfs`, `dd`, fork bombs, `DROP TABLE`, `find … -delete` at `/` or `~`). **Asks** on gray-area ones (`git reset --hard`, recursive/forced `rm`, `curl\|bash`, or sending a secret via `curl`/`wget`). Protected branches are configurable; chained commands (`&&` `;` `\|\|`) are split and each segment inspected. |
| [secrets_guard](docs/hooks/secrets_guard.md) | PreToolUse / `Read\|Edit\|Write\|Bash` | Denies reading/editing/`cat`-ing secret files (`.env` — `.env.example` is allowed — `*.pem`, `id_rsa`, `~/.ssh/`, `~/.aws/credentials`). Also **write-protects the hooks' own config and scripts** (`.claude-hooks.json`, `.claude/settings.json`, the installed `hooks/` and `rules/`) so the agent can't defang its own guards — reads still allowed. |
| [exfil_guard](docs/hooks/exfil_guard.md) | PreToolUse / `mcp__.*\|WebFetch\|WebSearch` | DLP inspection of outbound arguments (credentials, PII, confidentiality markers, custom patterns, optional semantic check). |
| [exfil_output_scan](docs/hooks/exfil_output_scan.md) | PostToolUse / `mcp__.*\|WebFetch\|WebSearch` | Detects secrets/PII in tool responses; configurable to warn or mask them. |
| [quality_gate](docs/hooks/quality_gate.md) | PostToolUse / `Edit\|Write` | Runs lint/format on the edited file and, on failure, blocks so Claude self-corrects (warn/block configurable). |
| [secrets_scan](docs/hooks/secrets_scan.md) | PostToolUse / `Edit\|Write` | Detects AWS keys, GitHub tokens, private-key blocks, etc. in written content and blocks. |
| [audit_log](docs/hooks/audit_log.md) | PreToolUse / PostToolUse / SessionStart / SessionEnd / Stop / `*` | Records every tool call and session boundary as JSONL, asynchronously. |
| [notify](docs/hooks/notify.md) | Notification | Notifies on permission-wait/idle. Default is an auto-detected desktop notification (bell fallback); bell-only or a custom command also available. |

## Customize

Every setting is **optional** — all guards run with safe defaults even with no config file at all. Configuration is for *tuning*, not for turning guards on. The deny tier can never be relaxed from a config file.

Put project-shared settings in `.claude-hooks.json` at your repo root; personal defaults go in `~/.claude/claude-hooks.json`. (These are the hooks' own config files — separate from Claude Code's `settings.json`, which only wires the hooks up and doesn't tune them.) A minimal example:

```json
{
  "bash_guard": {
    "extra_deny": ["docker system prune"],
    "protected_branches": ["main", "release"]
  },
  "exfil_guard": {
    "trusted_servers": ["mcp__internal-kb"]
  }
}
```

For the full schema, the 3-layer merge, and personal/team/high-security presets, see [docs/configuration.md](docs/configuration.md) (Japanese).

## Guarantees — and limits

**Guaranteed.** Deny-tier matches (`bash_guard` / `secrets_guard`) are deterministic regardless of Claude Code's permission mode, and **cannot be lifted from a config file** — not even with `enabled: false`, which only disables the softer ask tier. The only way to remove the deny tier is to remove the hook itself.

**Not guaranteed.** Hooks can be bypassed wholesale via Claude Code's `disableAllHooks` or by removing them — this is agent-accident prevention, not a malicious-user defense. Regex rules can't cover every unknown or obfuscated attack, and the optional semantic check is probabilistic (`ask` only). The [security model](docs/security-model.md) (Japanese) lays out the full picture, including specific known gaps.

## License / Contributing

- License: [LICENSE](LICENSE) (MIT)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
