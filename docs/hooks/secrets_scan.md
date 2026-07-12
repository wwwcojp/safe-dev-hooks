# secrets_scan

## 目的

`Edit`/`Write` で**書き込まれる内容そのもの**からシークレットを検出し、除去を促すために編集をblockする。

## 対象イベント / matcher

- イベント: `PostToolUse`
- matcher: `Edit|Write`(`hooks/hooks.json`)
- timeout: 10秒

スクリプト内部では `tool_name` が `Edit` / `Write` / `NotebookEdit` のいずれかであれば処理する実装になっているが、`hooks/hooks.json` の配線では matcher が `Edit|Write` のみのため、**既定の配線では `NotebookEdit` イベントはこのHookに到達しない**([既知の限界](#既知の限界)参照)。

## 判定基準

`tool_input` の `content` / `new_string` / `new_source` キー(存在するもののみ)を連結したテキストに対して、`rules/secret_patterns.json` の全ルールと `secrets_scan.custom_patterns` の追加ルールを適用する。

### block になる条件

以下いずれかのルールに一致するシークレットらしき文字列が書き込み内容に含まれる場合、`decision: "block"` を返し、該当ファイルパスとルール名を提示して除去・環境変数化を促す。

| ルール名 | 検出内容 |
|---|---|
| `aws-access-key` | `AKIA` + 英大文字数字16桁 |
| `github-token` | `gh[pousr]_` + 英数字36〜255桁 |
| `github-fine-grained-token` | `github_pat_` + 英数字等22〜255桁 |
| `slack-token` | `xox[baprs]-...` |
| `anthropic-api-key` | `sk-ant-...` |
| `private-key-block` | `-----BEGIN ... PRIVATE KEY-----` |
| `generic-credential` | `api_key`/`secret`/`token`/`passwd`/`password` に**クォート付き**で8文字以上の値を代入する形式(大文字小文字を無視) |

### 何もしない条件

いずれのルールにも一致しない場合。PII(メールアドレス等)はこのHookでは検査しない(PIIは `exfil_guard`/`exfil_output_scan` の担当領域)。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `secrets_scan.enabled` | `true` | falseで本Hookを無効化 |
| `secrets_scan.custom_patterns` | `[]` | ビルトインへマージする追加ルール。`[{"name": "...", "regex": "..."}]` 形式(`exfil_guard.custom_patterns` と同形式)。プロジェクト固有の禁止文字列(実ホームパス・内部ホスト名等)の書き込みブロックに使う |

allowlist(検出除外)を設定するキーは無い。

## 既知の限界

- **`NotebookEdit` は既定配線では発火しない**: コード自体は `NotebookEdit` の `new_source` を認識するが、`hooks/hooks.json` の `PostToolUse` matcher は `Edit|Write` のみのため、ノートブック編集を検査したい場合はユーザー側で matcher に `NotebookEdit` を追加する必要がある。
- **`generic-credential` はクォート必須**: `API_KEY=abcdefgh12345`(クォート無し)のような代入は正規表現がクォート文字を要求するため一致せず、検出されない。
- 検査対象は「今回の編集で書き込まれる差分文字列」のみであり、編集後のファイル全体は再走査しない。既存ファイル中の別箇所にあるシークレットは対象外。
- PII・機密マーカーは検査しない(必要であれば `exfil_output_scan`/`exfil_guard` のカテゴリを利用するか、`secrets_scan.custom_patterns` に個別パターンを追加する)。
- Hook自体の異常時は fail-open(検査スキップ、編集は通過)。
