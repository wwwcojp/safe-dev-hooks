# exfil_guard

## 目的

すべてのMCPツール(`mcp__*`)および組み込みの `WebFetch`/`WebSearch` への**送信引数**を検査し、認証情報・PII・機密マーカー・組織固有パターン・意味的に機微な情報を検出して `ask`/`deny` に変換する外部送信ガード(DLP)。

## 対象イベント / matcher

- イベント: `PreToolUse`
- matcher: `mcp__.*|WebFetch|WebSearch`
- timeout: 60秒、`statusMessage: "外部送信ペイロードを検査中"`(`hooks/hooks.json`)

`tool_input` 全体をJSON文字列化したものを検査ペイロードとする。

## 判定基準

`trusted_servers` に登録済みのMCPサーバー(`mcp__<server>` のプレフィックス一致)は検査自体をスキップする(`always` モードでも除外)。

### カテゴリと既定アクション

| カテゴリ | 検出内容 | 既定アクション | ルール定義 |
|---|---|---|---|
| `credentials` | AWSキー・GitHubトークン・Slackトークン・Anthropic APIキー・秘密鍵ブロック・`key=`/`token=`等の汎用形式(+ 任意でgitleaksの検出結果) | `deny` | `rules/secret_patterns.json`: `aws-access-key`, `github-token`, `github-fine-grained-token`, `slack-token`, `anthropic-api-key`, `private-key-block`, `generic-credential` |
| `pii` | メールアドレス・日本の電話番号・クレジットカード番号(Luhn検証)・マイナンバー(チェックデジット検証) | `ask` | `rules/pii_patterns.json`: `email`, `jp-phone`, `credit-card`, `my-number` |
| `confidential_markers` | 「社外秘」「部外秘」「極秘」「取扱注意」「マル秘」「㊙」「confidential」「internal only」等の文字列(大文字小文字を無視) | `ask` | `rules/confidential_markers.json` |
| `custom` | 組織固有の正規表現(社内ドメイン・コードネーム・顧客ID形式等) | `ask` | `.claude-hooks.json` の `exfil_guard.custom_patterns` |
| `semantic` | 機密マーカーが無くても機微と思われる情報(人事・給与・顧客情報・未公開の事業情報等)をヘッドレスClaude(`claude -p`)で判定 | `ask` 専用 | `rules/semantic_prompt.md` |

各カテゴリのアクションは `.claude-hooks.json` の `exfil_guard.categories` で `deny`/`ask`/`off` に上書きできる(`semantic` は `ask`/`off` のみ)。複数カテゴリが検出された場合、いずれかが `deny` なら全体判定は `deny`、そうでなければ `ask`。

`credentials` カテゴリの検出は `hooks/lib/scanners.py` の `scan_secrets()` に集約されており、内蔵の `rules/secret_patterns.json`(floor)を常に無条件で先に走らせたうえで、`scanners.gitleaks` が任意に有効な場合のみgitleaksの検出結果を加算する(union)。既定は `"auto"`(gitleaks不在なら無コストでスキップ)であり、gitleaksの不在・失敗はfloorの `deny` 判定に一切影響しない(fail-open)。`scanners.*` の設定は [docs/configuration.md](../configuration.md) を参照。

### 動作モード(`exfil_guard.mode`)

- **`detect`(既定)**: 上記カテゴリを検出した場合のみ `ask`/`deny`。何も検出されず、かつ `categories.semantic` が `off` でなければ semantic 判定を試みる。
- **`always`**: 対象ツール呼び出しを一律 `ask`(`trusted_servers` は除外)。ただしカテゴリ検査で `deny` が確定していれば `deny` のまま(`ask` へ降格しない)。

### semantic 判定の発火条件

以下をすべて満たす場合のみ `claude -p`(既定モデル `haiku`)を起動する:

1. `mode` が `detect` であり、正規表現ベースの `deny`/`ask` が未確定
2. `categories.semantic` が `off` ではない
3. `SAFE_DEV_HOOKS_SEMANTIC=1` 環境変数が未設定(ヘッドレスClaude呼び出し自体の再帰発火を防止するガード)
4. `claude` コマンドが `PATH` 上に存在する

ペイロード長による発火制限は設けない(D16)。短い検索クエリ等でも必ず判定が走るため、対象ツールの呼び出しごとにヘッドレスClaude実行のレイテンシ(数秒程度)とトークンコストが発生する点に注意。コストを抑えたい場合は `categories.semantic: "off"` または `trusted_servers` を利用する。

判定結果が `{"sensitive": true, ...}` であれば `ask`(理由に `reason` を含める)。`false`・タイムアウト(30秒)・JSON解析失敗・`claude` 不在時はいずれも判定なし(fail-open、素通り)。

semantic判定へ渡すペイロードはコスト・レイテンシ抑制のため先頭 `SEMANTIC_MAX_PAYLOAD`(4000文字)のみが対象であり、それ以降に含まれる機微情報は判定対象外となる。

### 何もしない条件

いずれのカテゴリにも一致せず、semantic 判定も機微なしと判断した(またはスキップされた)場合。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `exfil_guard.enabled` | `true` | falseで本Hookを無効化 |
| `exfil_guard.mode` | `"detect"` | `"detect"` = 検知時のみask / `"always"` = 一律ask(deny確定時は除く) |
| `exfil_guard.categories.credentials` | `"deny"` | `deny`/`ask`/`off` |
| `exfil_guard.categories.pii` | `"ask"` | `deny`/`ask`/`off` |
| `exfil_guard.categories.confidential_markers` | `"ask"` | `deny`/`ask`/`off` |
| `exfil_guard.categories.custom` | `"ask"` | `deny`/`ask`/`off` |
| `exfil_guard.categories.semantic` | `"ask"` | `ask`/`off` のみ |
| `exfil_guard.semantic.model` | `"haiku"` | ヘッドレス判定に使うモデル名(`claude -p --model` に渡す) |
| `exfil_guard.custom_patterns` | `[]` | `{"name": ..., "regex": ...}` の配列 |
| `exfil_guard.trusted_servers` | `[]` | 検査を完全スキップするMCPサーバーの `mcp__<server>` プレフィックス一覧 |

## 既知の限界

- **semantic判定は確率的(D11)**: LLM呼び出しによる判定のため検出漏れ・誤検出があり得る。`ask` にのみ使用し `deny` には昇格させない設計。判定失敗・タイムアウト・`claude` CLI不在時は fail-open(検査スキップ、素通り)となる。
- **正規表現+組織定義パターンで機械的に判定可能なものは確実に止め、それ以外はsemanticでベストエフォート検出**という保証レベルであり、文脈依存の機微情報(人名等)を正規表現側で完全に検出することはできない。
- semantic判定はペイロード先頭 `SEMANTIC_MAX_PAYLOAD`(4000文字)のみを対象とするため、それより後方にのみ機微情報が含まれる長大なペイロードは検出できない。
- semantic判定はペイロード長に関わらず必ず実行される(D16)ため、対象ツール呼び出しごとにレイテンシ・トークンコストが発生する。
- `always` モードでも `trusted_servers` は完全除外されるため、信頼済みサーバー設定を誤ると検査を素通りする。
- 判定不能な例外発生時は fail-open(`hook_io.fail_open`)であり、ツール実行は継続される(`systemMessage` で可視化はされる)。
