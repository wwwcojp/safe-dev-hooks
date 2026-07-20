# audit_log

## 目的

すべてのツール実行とセッション境界を JSONL 形式で非同期に記録し、エージェントが何をしたかの可視性を確保する。判定は一切行わず、ツール実行を止めることはない。

## 対象イベント / matcher

- イベント: `PreToolUse` / `PostToolUse` / `SessionStart` / `SessionEnd` / `Stop` / `ConfigChange`
- matcher: `*`(全ツール対象)
- `async: true`、timeout: 10秒(`hooks/hooks.json`)

`Notification` イベントはこのHookの配線には含まれない(通知の記録は行わない。[既知の限界](#既知の限界)参照)。

## 判定基準

このHookは `deny`/`ask`/`block` のいずれも返さない。常に記録のみを行い、`hook_io.finalize(None, cfg_all)` で正常終了する(設定エラー時のみ `systemMessage` を付与)。

記録するフィールド:

| フィールド | 内容 |
|---|---|
| `ts` | UTC ISO8601 タイムスタンプ |
| `session_id` | セッションID |
| `event` | `hook_event_name`(例: `PreToolUse`) |
| `tool_name` | ツール名 |
| `tool_summary` | `tool_input` をJSON文字列化し先頭500文字に切り詰めたもの |

出力先は `<audit_log.path>/audit-YYYYMMDD.jsonl`(UTC日付)への追記。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `audit_log.enabled` | `true` | falseで本Hookを無効化 |
| `audit_log.path` | `".claude/logs"` | ログ出力先ディレクトリ。相対パスは実行時の `cwd` からの相対パスとして解決される |

## 既知の限界

- **`tool_input` の先頭500文字のみ記録**: `tool_summary` は `SUMMARY_MAX_CHARS = 500` で切り詰められるため、長いペイロードは末尾が失われる。同時に、この500文字の中に機密情報(シークレット・PIIの断片)がそのまま残り得る。ログファイル自体は `.gitignore` で除外されている(`logs/`, `.claude/logs/`, `*.jsonl`)ため、リポジトリへのコミットは防がれるが、**ローカルディスク上には機微情報を含むログが残る**ことに留意が必要(詳細は [docs/security-model.md](../security-model.md) 参照)。
- **`tool_output`/応答本文は記録しない**: 記録対象は `tool_input` のみで、`PostToolUse` であっても実行結果は記録されない。
- **`Notification` イベントは対象外**: `hooks/hooks.json` の配線上、通知イベントは `notify` のみが処理し `audit_log` には記録されない。
- 書き込み失敗(ディスク容量不足・権限エラー等)は例外を握りつぶして無視する(スペック セクション8の方針どおり、監査ログの失敗で開発を止めない)。そのため記録漏れが発生していても気づけない場合がある。
- ログファイルにはローテーション機構が無く、日付ごとに増え続ける。
