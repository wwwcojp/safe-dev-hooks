# exfil_output_scan

## 目的

MCPツール・`WebFetch`・`WebSearch` の**応答**に含まれるシークレット・PIIを検出し、Claudeへの警告表示または応答本文のマスキングを行う。

## 対象イベント / matcher

- イベント: `PostToolUse`
- matcher: `mcp__.*|WebFetch|WebSearch`
- timeout: 15秒(`hooks/hooks.json`)

`tool_output`(無ければ `tool_response`)を文字列化して検査する(文字列でなければJSON文字列化)。

## 判定基準

`rules/secret_patterns.json`(`aws-access-key`, `github-token`, `github-fine-grained-token`, `slack-token`, `anthropic-api-key`, `private-key-block`, `generic-credential`)と `rules/pii_patterns.json`(`email`, `jp-phone`, `credit-card`, `my-number`)の全ルールを応答テキストに適用する。`exfil_guard` と異なり、`confidential_markers` と `custom_patterns` はここでは検査対象外(応答からの流入検知はシークレット・PIIのみ)。

検出が1件でもあれば `exfil_output_scan.action` に応じて以下いずれかを返す:

- **`warn`(既定)**: `hookSpecificOutput.additionalContext` に検出ルール名を含む注意文を追加(ツール応答自体は変更しない)
- **`redact`**: 検出した各マッチ文字列を `[REDACTED:<ルール名>]` に置換した応答を `hookSpecificOutput.updatedToolOutput` として返す。**ただし `tool_output`/`tool_response` が文字列型の場合のみ**マスキングが行われる。文字列でない(オブジェクト等の)応答の場合は `redact` を設定していても `warn` と同じ注意文のみが返る([既知の限界](#既知の限界)参照)。

検出が無ければ何も出力しない。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `exfil_output_scan.enabled` | `true` | falseで本Hookを無効化 |
| `exfil_output_scan.action` | `"warn"` | `"warn"` = 注意喚起のみ / `"redact"` = マスキング(文字列応答のみ) |

## 既知の限界

- **redactの上限は1ルールにつき20件(D12)**: `scan_text` は同一ルール内で重複しない完全一致文字列を最大 `MAX_FINDINGS_PER_RULE = 20` 件まで収集する。1つの応答に同一ルールで21件目以降の異なるシークレットが含まれる場合、その21件目以降はマスキングされずに応答へ残る。
- **`redact` は文字列応答のみに有効**: `tool_output`/`tool_response` が文字列でない場合(構造化データを返すMCPツール等)、`action: "redact"` を設定していてもマスキングは行われず `warn` 相当の注意文のみが返る。応答本文はそのままClaudeに渡る。
- `confidential_markers` と `custom_patterns` はこのHookでは検査されない(`exfil_guard` の入力検査側のみ対象)。応答経由でこれらの機密マーカーが漏れても検出されない。
- 判定不能な例外発生時は fail-open(検査スキップ、応答はそのまま通過)。
