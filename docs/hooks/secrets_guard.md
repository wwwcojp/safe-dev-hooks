# secrets_guard

## 目的

`.env` や秘密鍵、クラウド認証情報など機密ファイルへの読取・編集・書込・Bash経由のアクセスを遮断する。

## 対象イベント / matcher

- イベント: `PreToolUse`
- matcher: `Read|Edit|Write|Bash`
- timeout: 10秒(`hooks/hooks.json`)

## 判定基準

`Read`/`Edit`/`Write` の場合は `tool_input.file_path` を直接検査する。`Bash` の場合はコマンドを `shlex.split` でトークン分割し、**パス形式に見えるトークンのみ**を検査対象にする(`_looks_like_path`: `/` を含む、`.`/`~` で始まる、または `.` を含む。ただし `*`/`?`/`[` を含むトークン(globパターン)は除外)。

トークン(または `file_path`)が以下のいずれかに一致すれば `deny`:

### deny になる条件(`rules/sensitive_paths.json` の `protected` / `protected_dirs`)

- ファイル名パターン: `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `*.p12`, `*.pfx`, `*.keystore`, `.credentials.json`, `credentials`, `.netrc`, `.npmrc`, `.pypirc`, `secrets.*`
- ディレクトリ: `~/.ssh`, `~/.aws`, `~/.gnupg` 以下すべて
- `.claude-hooks.json` の `secrets_guard.protected_paths` に追加したパターンもマージされる

### 何もしない条件(allow)

- `rules/sensitive_paths.json` の `allow`: `.env.example`, `.env.sample`, `.env.template`, `*.pub`
- `.claude-hooks.json` の `secrets_guard.allow_paths` に追加したパターン
- allowは protected より先に評価されるため、allowに一致すれば無条件で許可される
- 上記いずれの保護パターン・保護ディレクトリにも一致しない、かつ `Bash` の場合はパス形式に見えないトークン(例: 検索キーワードやglobパターン)

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `secrets_guard.enabled` | `true` | falseで本Hookを無効化 |
| `secrets_guard.protected_paths` | `[]` | 追加で保護するファイル名/パスのglobパターン(ビルトインへマージ) |
| `secrets_guard.allow_paths` | `[]` | 追加で許可するファイル名/パスのglobパターン(ビルトインへマージ) |

## 既知の限界

- **裸のファイル名(拡張子なし)は検知漏れになる(D13)**: Bashコマンドのトークン検査は「パス形式のトークン」のみを対象にしている。`grep credentials` や `find -name "*.pem"` のような検索コマンドまで解除不能denyにすると実用性を損なうためのトレードオフ。結果として、`cat credentials`(パス区切り・ドット・チルダを含まない裸のファイル名)のような直接アクセスは検査対象から外れ、**検知漏れとなる**。一方 `~/.aws/credentials` のようなパス形式であれば `_looks_like_path` に合致し捕捉される。
- `Read`/`Edit`/`Write` の `file_path` は常に検査されるため、上記の限界は Bash 経由のアクセスにのみ適用される。
- シンボリックリンクや `..` を用いたパストラバーサルで実体パスが変わっても、文字列としてのパターンマッチのみで判定するため、表記次第では見逃す・過検知する可能性がある。
- 判定不能時は fail-close(`ask`)。
