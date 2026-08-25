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
| `tool_summary` | `tool_input` を構造を保ったままJSON文字列化し、`SUMMARY_MAX_CHARS`(500)文字以内に収めたもの。常に `json.loads` できる形で切り詰める(詳細は下記)。 |

出力先は `<audit_log.path>/audit-YYYYMMDD.jsonl`(UTC日付)への追記。

### `tool_summary` の切り詰め方式(構造保存トランケーション)

`tool_summary` は文字列フィールドのまま(オンディスクの形は変えていない)だが、中身のJSONを直列化した**後**に単純スライスするのではなく、直列化する**前**に構造(値の長さ・キー/要素数・入れ子の深さ)を切り詰める。これにより `tool_summary` は常に妥当なJSON文字列になる(`json.loads(record["tool_summary"])` が常に成功する)。

切り詰めが発生した箇所は次のマーカーで機械的に検出できる(`hooks/audit/audit_log.py` の定数):

| マーカー | 意味 |
|---|---|
| 文字列値の末尾に付く `…[+N c]`(`TRUNCATED_TAG_PREFIX = "…[+"`) | その値はN文字省略された。数値など非文字列の値が長すぎる場合も文字列化した上で同じ形式で切り詰める。 |
| `"__omitted_keys__": N`(`OMITTED_KEYS_KEY`) | そのdictでN個のキーを丸ごと省略した(先頭から一定数のみ保持)。 |
| `{"__omitted_items__": N}`(`OMITTED_ITEMS_KEY`) をlistの末尾要素として追加 | そのlistでN個の要素を丸ごと省略した。 |
| `"__audit_truncated__": true`(`TRUNCATED_MARKER_KEY`) | 何らかの切り詰めがそのdict/list階層で発生した。トップレベルがdict/listでない単一値(文字列・数値等)の場合はこのキーを付与できないため、値そのものの末尾タグのみが切り詰めの印になる。 |

これらのキー名は予約済みで、`tool_input` に同名のキーが実在する場合は区別できない(実運用上のツールスキーマでは想定していない)。

並外れて多い巨大キーが与えられるなど、上記の縮小(値の長さ→キー/要素数の順に段階的に縮小)でも `SUMMARY_MAX_CHARS` に収まらない病的な入力に対しては、最終手段として `{"__audit_truncated__": true}` のみの最小オブジェクトを返す。

**過去ログとの互換性**: この方式は 0.7.2 以前の記録には遡って適用されない。0.7.2 以前の `tool_summary` は直列化後の単純スライスで、500文字ちょうどで切れたレコードは `json.loads` に失敗し得る(実測: 17日分・3プロジェクトのBashレコード13,968件中2,126件・15%が該当)。既存のログファイルはそのまま残る。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `audit_log.enabled` | `true` | falseで本Hookを無効化 |
| `audit_log.path` | `".claude/logs"` | ログ出力先ディレクトリ。相対パスは**プロジェクトルート**からの相対パスとして解決される(0.7.1)。絶対パスを指定した場合は従来どおりそのまま使う |

相対パスの基準となる「プロジェクトルート」は `hooks/lib/config.py` の `project_root(cwd)` が次の順で決定する: (1) 環境変数 `CLAUDE_PROJECT_DIR`(空文字でない・絶対パスである・実在するディレクトリである・`cwd` の祖先であるの4条件をすべて満たす場合のみ採用。条件の詳細は [docs/configuration.md](../configuration.md) §1)、(2) 無ければ `cwd` から見た最近傍の祖先で `.git` が存在するディレクトリ(git worktreeでは `.git` はファイルだが同様に扱う)、(3) それも無ければ従来どおり `cwd`。0.7.0以前は常に `cwd`(Bashの `cd` に追従する一時的な作業ディレクトリ)基準だったため、Claudeがサブディレクトリへ移動した状態で発火したフックでは監査ログが作業ディレクトリ配下に散らばっていた(0.7.1で修正)。詳細: [docs/configuration.md](../configuration.md) §1、[docs/security-model.md](../security-model.md)。

## 既知の限界

- **`tool_input` は `SUMMARY_MAX_CHARS`(500文字)相当までしか記録しない**: 上記の構造保存トランケーションで妥当なJSONは保つが、長いペイロードや多数のキーは末尾・一部が省略される(省略箇所は `__omitted_keys__`/`__omitted_items__`/末尾の `…[+N c]` タグでマーキング)。同時に、記録される範囲に機密情報(シークレット・PIIの断片)がそのまま残り得る。ログファイル自体は `.gitignore` で除外されている(`logs/`, `.claude/logs/`, `*.jsonl`)ため、リポジトリへのコミットは防がれるが、**ローカルディスク上には機微情報を含むログが残る**ことに留意が必要(詳細は [docs/security-model.md](../security-model.md) 参照)。
- **`tool_output`/応答本文は記録しない**: 記録対象は `tool_input` のみで、`PostToolUse` であっても実行結果は記録されない。
- **`Notification` イベントは対象外**: `hooks/hooks.json` の配線上、通知イベントは `notify` のみが処理し `audit_log` には記録されない。
- 書き込み失敗(ディスク容量不足・権限エラー等)は例外を握りつぶして無視する(スペック セクション8の方針どおり、監査ログの失敗で開発を止めない)。そのため記録漏れが発生していても気づけない場合がある。
- ログファイルにはローテーション機構が無く、日付ごとに増え続ける。
