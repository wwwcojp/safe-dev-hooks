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

### write_protected: 設定/フックファイルの改変遮断

読取(`Read`)は許可したまま、この Hooks 集自身の設定・スクリプトファイルへの**改変**を deny する。

- 対象(`rules/sensitive_paths.json` の `write_protected`): `.claude-hooks.json`, `claude-hooks.json`, `settings.json`, `settings.local.json`, `hooks.json`、およびこのインストール自身の `hooks/`/`rules/` ディレクトリ配下すべて(実パスを解決して判定)
- `.claude-hooks.json` の `secrets_guard.write_protected_paths` に追加したパターンもマージされる(解除は不可)
- `Edit`/`Write` は `file_path` が対象に一致すれば deny。`Read` は対象外(閲覧は妨げない)
- `Bash` はコマンド中に変異キーワード(`>`/`>>` によるリダイレクト。トークンに密着した `>file` を含む、`dd of=`、`rm`/`mv`/`cp`/`sed -i`/`tee`/`truncate`/`ln`/`install`)を含むセグメントのみを検査し、変異先のパス形式トークンが対象に一致すれば deny

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `secrets_guard.enabled` | `true` | falseにしても deny 層(保護パス・write_protected)は解除できない(no-op)。ask層自体を持たないため実質的に無効化ボタンではなく、`systemMessage` で「enabled:false でも deny 層を無効化できません」と通知したうえで検査を継続する |
| `secrets_guard.protected_paths` | `[]` | 追加で保護するファイル名/パスのglobパターン(ビルトインへマージ) |
| `secrets_guard.allow_paths` | `[]` | 追加で許可するファイル名/パスのglobパターン(ビルトインへマージ) |
| `secrets_guard.write_protected_paths` | `[]` | 追加で書込保護するファイル名/パスのglobパターン(ビルトインへマージ、解除不可) |

## 既知の限界

- **裸のファイル名(拡張子なし)は検知漏れになる(D13)**: Bashコマンドのトークン検査は「パス形式のトークン」のみを対象にしている。`grep credentials` や `find -name "*.pem"` のような検索コマンドまで解除不能denyにすると実用性を損なうためのトレードオフ。結果として、`cat credentials`(パス区切り・ドット・チルダを含まない裸のファイル名)のような直接アクセスは検査対象から外れ、**検知漏れとなる**。一方 `~/.aws/credentials` のようなパス形式であれば `_looks_like_path` に合致し捕捉される。
- `Read`/`Edit`/`Write` の `file_path` は常に検査されるため、上記の限界は Bash 経由のアクセスにのみ適用される。
- シンボリックリンクや `..` を用いたパストラバーサルで実体パスが変わっても、文字列としてのパターンマッチのみで判定するため、表記次第では見逃す・過検知する可能性がある。
- **write_protectedはシェル変異キーワードが無いインタプリタ書き込みを検知できない**: Bash経由のwrite_protected検査は `>`/`dd of=`/`rm`/`mv`/`cp`/`sed -i`/`tee`/`truncate`/`ln`/`install` 等の既知の変異キーワードを含むセグメントのみを対象にしている。`python3 -c "open('.claude-hooks.json','w').write(...)"` のように、シェルレベルの変異キーワードを一切使わずインタプリタ内で直接ファイルへ書き込むコマンドは検査を素通りする。正規表現+機械判定できる範囲を確実に止めるというこのHooks集全体の設計方針([docs/security-model.md](../security-model.md) §3)の帰結であり、write_protectedも例外ではない。
- **enabled:false はdeny層(write_protectedを含む)を無効化しない**: `secrets_guard.enabled: false` を設定しても、保護パスの検査もwrite_protectedの検査も継続する。`systemMessage` でその旨を通知するのみで、動作は変わらない。deny層を止める唯一の正規手段はHookを `hooks/hooks.json` から除去すること、または Claude Code 本体の `disableAllHooks` である。
- 判定不能時は fail-close(`ask`)。
