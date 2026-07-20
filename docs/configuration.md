# 設定リファレンス(`.claude-hooks.json`)

## 1. 3層マージ

設定は次の3層を、下から上へ(ビルトイン既定 → グローバル → プロジェクト)マージして決定する。上位ほど優先度が高い。

| 優先度 | ファイル | 用途 |
|---|---|---|
| 1(最優先) | プロジェクト直下の `.claude-hooks.json` | チームで共有する設定(コミット対象) |
| 2 | `~/.claude/claude-hooks.json` | 利用者ごとの個人既定値 |
| 3(最下位) | 同梱の `hooks/lib/config.py` 内 `DEFAULTS` | ビルトインの安全側既定値 |

設定ファイルが1つも無くても、全ガードはビルトイン既定値で動作する(「設定は有効化ではなく調整のため」という設計原則)。

### マージの規則

- キーごとの再帰的ディープマージ(`hooks/lib/config.py` の `_merge`)。
- オブジェクト(`{...}`)は再帰的にマージされる。
- **配列・文字列・真偽値は、上位の層の値で丸ごと置き換わる(配列は追記ではなく置換)。** 例: プロジェクト設定で `bash_guard.extra_deny` を指定すると、グローバル設定の同キーは使われず置き換わる。

### どの層に何を置くか

- **チーム共有**の設定 → コミット対象のプロジェクト `.claude-hooks.json`。
- **マシン固有の値**(例: `notify.command` の絶対パス)や**個人の既定値** → グローバルの `~/.claude/claude-hooks.json`。
- Claude Code 本体の `settings.json` / `settings.local.json` は本プラグインの設定読み込み対象ではない(混同しやすいので注意)。

### 設定エラー時の挙動(常に安全側)

不正な設定は無視され、該当箇所だけビルトイン既定値へフォールバックしたうえで `systemMessage` で警告する(検査自体は止めない)。

- **型不一致**(セクションの型が既定と違う)→ そのセクションを既定へ。
- **JSON構文エラー / オブジェクトでない設定ファイル** → そのファイルを無視。
- **列挙値のタイポ** → 該当キーのみ既定へ。対象は `exfil_guard.mode`・`exfil_output_scan.action`・`quality_gate.mode`、および `exfil_guard.categories` の各値(`deny`/`ask`/`off`)。既定に無い未知のカテゴリキーは削除する。

いずれも `_errors` に1件ずつ記録され、Hook出力に `[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: ...` が付く。

## 2. 全スキーマ

以下は実装(`hooks/lib/config.py` の `DEFAULTS`)と一致する全キー・既定値である。

```jsonc
{
  "bash_guard": {
    "enabled": true,
    "extra_deny": [],                        // 追加のdeny正規表現(解除不可)
    "extra_ask": [],                         // 追加のask正規表現
    "allow": [],                             // ask層のみ解除可能な正規表現(deny層は解除不可)
    "protected_branches": ["main", "master", "develop", "release", "production"]
                                              // force-push denyの対象ブランチ(refspec送信先も判定)
                                              // 空リスト [] にすると force-push の deny 昇格を無効化(ask層では拾われ得る)
  },
  "secrets_guard": {
    "enabled": true,
    "protected_paths": [],                   // 追加で保護するファイル名/パスのglobパターン
    "allow_paths": [],                       // 追加で許可するファイル名/パスのglobパターン
    "write_protected_paths": []              // 追加で書込保護するファイル名/パスのglobパターン(ビルトインへマージ、解除不可)
  },
  "exfil_guard": {
    "enabled": true,
    "mode": "detect",                        // "detect"=検知時のみask / "always"=一律ask
    "categories": {
      "credentials": "deny",                 // deny | ask | off
      "pii": "ask",                          // deny | ask | off
      "confidential_markers": "ask",         // deny | ask | off
      "custom": "ask",                       // deny | ask | off
      "semantic": "ask"                      // ask | off(semanticはdeny不可)
    },
    "semantic": {
      "model": "haiku"                       // ヘッドレス判定(claude -p --model)に使うモデル
    },
    "custom_patterns": [],                   // [{ "name": "...", "regex": "..." }, ...]
    "trusted_servers": []                    // ["mcp__internal-kb", ...] 検査スキップ対象
  },
  // 注: semantic判定はペイロード長に関わらず必ず実行される(D16)ため、
  // MCP/WebFetch/WebSearchの呼び出しごとにヘッドレスClaude実行のレイテンシ(数秒程度)と
  // トークンコストが発生する。重い場合は categories.semantic を "off" にするか、
  // 信頼できるサーバーを trusted_servers に登録して検査自体をスキップする。
  "exfil_output_scan": {
    "enabled": true,
    "action": "warn"                         // "warn" | "redact"(redactは文字列応答のみ有効)
  },
  "quality_gate": {
    "enabled": true,
    "mode": "block",                         // "block"=Claudeに修正させる / "warn"=注記のみ
    "commands": {}                           // { "<ファイル名glob>": ["ruff check {file}", ...] }
  },
  "secrets_scan": {
    "enabled": true,
    "custom_patterns": []                    // [{ "name": "...", "regex": "..." }, ...] ビルトインへマージ
  },
  "audit_log": {
    "enabled": true,
    "path": ".claude/logs"                   // 相対パスはcwd起点
  },
  "config_guard": {
    "enabled": true                          // セッション中の設定変更(ConfigChange)を通知。警告専用のためfalseで無効化可
  },
  "notify": {
    "enabled": true,
    "method": "auto",                        // "auto"=デスクトップ通知の自動判別(不可ならベル) / "bell"=常にベル
    "command": null                          // 設定時はmethodより優先。{message} 置換で実行
  }
}
```

各Hookの設定キーの詳細は個別のHookリファレンスも参照してください: [bash_guard](hooks/bash_guard.md) / [secrets_guard](hooks/secrets_guard.md) / [exfil_guard](hooks/exfil_guard.md) / [exfil_output_scan](hooks/exfil_output_scan.md) / [quality_gate](hooks/quality_gate.md) / [secrets_scan](hooks/secrets_scan.md) / [audit_log](hooks/audit_log.md) / [config_guard](hooks/config_guard.md) / [notify](hooks/notify.md)。

## 3. 設計原則

- **安全側の既定**: 設定ファイルが無くても全ガードが既定値で動く。
- **denyの解除は不可**: `bash_guard.allow` で解除できるのは ask 層のみ。回復不能系 deny は設定ファイルからは一切解除できない。`enabled: false` は deny 層を解除しない — `bash_guard.enabled: false` は ask 層(`bash_ask.json`・`extra_ask`・curl/wgetの外部送信ask検査)のみを無効化し、`secrets_guard.enabled: false` はdeny層に対してはno-op(`systemMessage` で通知のうえ検査を継続)である。deny層を止める唯一の正規手段は `hooks/hooks.json` からのHook除去、または Claude Code 本体の `disableAllHooks` である。
- **データ駆動**: 危険パターン・シークレット形式・PII形式は `rules/*.json` に集約されており、コード変更なしで拡張可能。`extra_deny`/`extra_ask`/`custom_patterns`/`protected_paths`/`allow_paths` はビルトインへマージされる。
- **quality_gateの自動検出**: `commands` 未指定時は拡張子と `pyproject.toml`(ruff)、`package.json`(eslint)、`Cargo.toml`(rustfmt)等から推定する。検出不能なら何もしない。

## 4. 設定プリセット例

### 4.1 個人用(既定のまま)

個人利用では設定ファイルを作らない、または空の `{}` で十分。全ガードがビルトイン既定値(安全側)で動作する。

```json
{}
```

### 4.2 チーム用(`custom_patterns` + `trusted_servers` 追加)

社内ドメインへの言及を検出し、社内ナレッジベースMCPサーバーは検査から除外する例。

```json
{
  "bash_guard": {
    "extra_deny": ["docker system prune -a"],
    "protected_branches": ["main", "master", "develop", "release", "production"]
  },
  "exfil_guard": {
    "custom_patterns": [
      { "name": "社内ドメイン", "regex": "[\\w.-]+\\.example\\.co\\.jp" },
      { "name": "顧客ID", "regex": "\\bCUST-\\d{6}\\b" }
    ],
    "trusted_servers": ["mcp__internal-kb", "mcp__internal-docs"]
  },
  "quality_gate": {
    "commands": {
      "*.py": ["ruff check {file}", "ruff format --check {file}"]
    }
  }
}
```

### 4.3 高セキュリティ(`exfil_guard.mode=always`, `exfil_output_scan.action=redact`)

すべての外部送信を一律askにし、応答からのシークレット・PII漏洩はマスキングして遮断する例。

```json
{
  "exfil_guard": {
    "mode": "always",
    "categories": {
      "credentials": "deny",
      "pii": "deny",
      "confidential_markers": "ask",
      "custom": "ask",
      "semantic": "ask"
    },
    "trusted_servers": []
  },
  "exfil_output_scan": {
    "action": "redact"
  },
  "secrets_guard": {
    "protected_paths": ["config/secrets/**", "**/*.credentials"],
    "write_protected_paths": ["deploy/*.lock", "infra/**/*.tfstate"]
  }
}
```

`exfil_output_scan.action: "redact"` は `tool_output`/`tool_response` が文字列型の応答にのみ有効([docs/hooks/exfil_output_scan.md](hooks/exfil_output_scan.md) の既知の限界を参照)。
