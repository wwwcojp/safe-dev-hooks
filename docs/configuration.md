# 設定リファレンス(`.claude-hooks.json`)

## 1. 3層マージ

設定は次の3層を、下から上へ(ビルトイン既定 → グローバル → プロジェクト)マージして決定する。上位ほど優先度が高い。

| 優先度 | ファイル | 用途 |
|---|---|---|
| 1(最優先) | プロジェクト直下の `.claude-hooks.json` | チームで共有する設定(コミット対象) |
| 2 | `~/.claude/claude-hooks.json` | 利用者ごとの個人既定値 |
| 3(最下位) | 同梱の `hooks/lib/config.py` 内 `DEFAULTS` | ビルトインの安全側既定値 |

マージはキーごとの再帰的ディープマージ(`hooks/lib/config.py` の `_merge`)で行われる。オブジェクト値は再帰的にマージされ、それ以外(配列・文字列・真偽値等)は上位の値で丸ごと置き換えられる(**配列は追記ではなく置換**。例えば `bash_guard.extra_deny` をプロジェクト設定で指定すると、グローバル設定の同キーの値は使われず置き換わる)。

設定ファイルが1つも存在しなくても、全ガードはビルトイン既定値で動作する(「設定は調整のためであり、有効化のためではない」という設計原則)。

### 設定自体の検証

起動時に各セクションの型をビルトイン既定値の型と比較し、不一致であれば当該セクションのみビルトイン既定値へフォールバックする。フォールバックが発生した場合、Hookの出力に `systemMessage` として `[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: ...` という警告が付与される(判定自体は継続される)。JSON構文エラーやオブジェクトでない設定ファイルも同様に無視され、警告のみで安全側の既定値が使われる。

## 2. 全スキーマ

以下は実装(`hooks/lib/config.py` の `DEFAULTS`)と一致する全キー・既定値である。

```jsonc
{
  "bash_guard": {
    "enabled": true,
    "extra_deny": [],                        // 追加のdeny正規表現(解除不可)
    "extra_ask": [],                         // 追加のask正規表現
    "allow": []                              // ask層のみ解除可能な正規表現(deny層は解除不可)
  },
  "secrets_guard": {
    "enabled": true,
    "protected_paths": [],                   // 追加で保護するファイル名/パスのglobパターン
    "allow_paths": []                        // 追加で許可するファイル名/パスのglobパターン
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
      "model": "haiku",                      // ヘッドレス判定(claude -p --model)に使うモデル
      "min_payload_chars": 200               // これ未満のペイロードは判定スキップ
    },
    "custom_patterns": [],                   // [{ "name": "...", "regex": "..." }, ...]
    "trusted_servers": []                    // ["mcp__internal-kb", ...] 検査スキップ対象
  },
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
    "enabled": true
  },
  "audit_log": {
    "enabled": true,
    "path": ".claude/logs"                   // 相対パスはcwd起点
  },
  "notify": {
    "enabled": true,
    "command": null                          // nullならターミナルベル。文字列なら {message} 置換で実行
  }
}
```

各Hookの設定キーの詳細は個別のHookリファレンスも参照してください: [bash_guard](hooks/bash_guard.md) / [secrets_guard](hooks/secrets_guard.md) / [exfil_guard](hooks/exfil_guard.md) / [exfil_output_scan](hooks/exfil_output_scan.md) / [quality_gate](hooks/quality_gate.md) / [secrets_scan](hooks/secrets_scan.md) / [audit_log](hooks/audit_log.md) / [notify](hooks/notify.md)。

## 3. 設計原則

- **安全側の既定**: 設定ファイルが無くても全ガードが既定値で動く。
- **denyの解除は不可**: `bash_guard.allow` で解除できるのは ask 層のみ。回復不能系 deny を外すには Hook 自体の無効化(`enabled: false` または `hooks.json` からの除去)しかない。
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
    "extra_deny": ["docker system prune -a"]
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
    "protected_paths": ["config/secrets/**", "**/*.credentials"]
  }
}
```

`exfil_output_scan.action: "redact"` は `tool_output`/`tool_response` が文字列型の応答にのみ有効([docs/hooks/exfil_output_scan.md](hooks/exfil_output_scan.md) の既知の限界を参照)。
