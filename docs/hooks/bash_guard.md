# bash_guard

## 目的

Bashで実行されようとしているコマンドを検査し、回復不能な破壊的操作を `deny`、注意が必要なグレーな操作を `ask` として段階的にガードする。

## 対象イベント / matcher

- イベント: `PreToolUse`
- matcher: `Bash`
- timeout: 10秒(`hooks/hooks.json`)

`tool_name` が `Bash` 以外のイベントは即座に何もせず終了する。

## 判定基準

コマンド文字列を `&&` `||` `;` `\n` で分割したうえで、分割前の全体文字列も含めて各セグメントに対して正規表現ルールを適用する。判定前にダブルクォート・シングルクォートを除去してから照合する(過剰検知側に倒す設計。[既知の限界](#既知の限界)参照)。

判定に先立ち、同一コマンド内の単純な定数代入(`VAR=値` の形。クォート・`$(...)`・変数参照を含まないもの)を後続の `$VAR`/`${VAR}` へ展開したうえでも判定する(例: `T=/; rm -rf $T` は展開後の `rm -rf /` が `rm-root-or-home` に一致し `deny`)。動的な値(コマンド置換、コマンド外で設定された環境変数)は展開できないため、この経路のdeny判定には届かない([既知の限界](#既知の限界)参照)。

### deny になる条件(`rules/bash_deny.json` + force-pushルール。設定で解除不可)

| ルール名 | 意味 |
|---|---|
| `rm-root-or-home` | `/`・`~`・`$HOME` を対象にした `rm`(フラグの有無に関わらず)。`rm -rf /.`・`rm -rf /..` のような末尾ドットによる回避も対象 |
| `find-delete-root` | `find /`・`find ~`・`find $HOME` 配下に対する `-delete` / `-exec rm` |
| `rm-system-dir` | `/etc` `/usr` `/var` `/bin` `/boot` `/lib` `/opt` `/srv` を対象にした `rm` |
| `rm-home-root` | `/home/<user>`(Linux)または `/Users/<user>`(macOS)直下そのものを対象にした `rm`(ユーザーホーム全体の削除のみ。`/home/<user>/sub/...` のような深いサブパスは対象外で、ask層の `rm-recursive-or-force` に委ねる) |
| `sudo-rm` | `sudo rm` |
| `force-push-protected` / `force-push-protected-order` | `bash_guard.protected_branches`(既定 `main`/`master`/`develop`/`release`/`production`)への `--force`/`-f` force push(引数順の入れ替えにも対応) |
| `force-push-refspec` | `+` refspec形式の force push(例: `git push origin +HEAD:main`)。**refspecの送信先(コロンの右側)** が `protected_branches` に一致する場合のみdeny。`git push origin +main:feature` のように送信先が保護対象外であれば、ローカル側が `main` でもdenyにならない([既知の限界](#既知の限界)参照) |
| `mkfs` | `mkfs` 系コマンド |
| `dd-to-device` | `dd ... of=/dev/...` |
| `fork-bomb` | `:(){ :\|:& };:` 形式のfork bomb |
| `chmod-777-root` | `chmod 777 /` |
| `redirect-to-device` | `/dev/sd[a-z]` への出力リダイレクト |
| `sql-drop` | `psql`/`mysql`/`mariadb`/`sqlite3`/`sqlcmd` によるSQLクライアント実行文脈での `DROP TABLE` / `DROP DATABASE`(大文字小文字を無視。`grep`/`echo`/コミットメッセージ中の文字列一致は対象外) |
| `sql-truncate` | 同上のSQLクライアント実行文脈での `TRUNCATE TABLE`(大文字小文字を無視。文字列一致のみは対象外) |

加えて `.claude-hooks.json` の `bash_guard.extra_deny`(正規表現の配列)がビルトインdenyルールへマージされる。**deny層は `bash_guard.enabled: false` でも無効化されない**(後述)。

### ask になる条件(`rules/bash_ask.json` + 外部送信ask検査)

| ルール名 | 意味 |
|---|---|
| `git-reset-hard` | `git reset --hard` |
| `git-clean-force` | `git clean -f` 系 |
| `git-force-push` | `protected_branches` 以外への force push |
| `rm-recursive-or-force` | `-r`/`-f` を含む `rm`(フラグ最大8個までの間に挟まれたオプションを許容。[既知の限界](#既知の限界)参照) |
| `pipe-to-shell` | `curl`/`wget` の出力を `sh`/`bash`/`zsh`/`dash` にパイプ |
| `npm-publish` | `npm publish` |
| `git-discard-worktree` | `git checkout .` / `git restore .` |
| `find-delete` | root/home以外を対象にした `find ... -delete` / `-exec rm`(root/home対象は上記 `find-delete-root` でdeny) |
| (ルール名なし、外部送信ask検査) | `curl`/`wget` がデータ送信フラグ(`-d`/`--data*`/`-F`/`--form`/`-T`/`--upload-file`/`--post-data`/`--post-file`/`--body-data`/`--body-file`)と機微オペランド(環境変数参照 `$VAR`/`${VAR}`、コマンド置換 `$(...)`/`` ` ``、または `sensitive_paths.json` の保護ファイル名)を同時に含む場合。`exfil_guard` はMCP/WebFetch/WebSearchのみを対象にしており、bash経由の外部送信は検査対象外だったため、その隙間をcurl/wgetに限定して埋めるもの。`scp`/`rsync`/`nc` 等は対象外で、`deny` へは昇格しない |

`.claude-hooks.json` の `bash_guard.extra_ask` がビルトインaskルールへマージされる。`bash_guard.allow`(正規表現の配列)に一致するセグメントは ask 判定(外部送信ask検査を含む)から除外される(**ask層のみ解除可能。deny層は解除不可**)。**`bash_guard.enabled: false` はask層(本表全体)を丸ごと無効化する。**

### 何もしない条件

どのdeny/askルールにも一致しないコマンド。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `bash_guard.enabled` | `true` | falseで **ask層のみ** を無効化。deny層は解除できない(常に動作する) |
| `bash_guard.extra_deny` | `[]` | 追加のdeny正規表現(ビルトインへマージ、解除不可) |
| `bash_guard.extra_ask` | `[]` | 追加のask正規表現(ビルトインへマージ) |
| `bash_guard.allow` | `[]` | ask層の判定から除外する正規表現(deny層には無効) |
| `bash_guard.protected_branches` | `["main", "master", "develop", "release", "production"]` | force-push denyの対象ブランチ(通常のrefspecと `+` refspec送信先の両方に適用) |

## 既知の限界

- **`enabled: false` はdeny層を無効化しない**: `bash_guard.enabled: false` はask層(`bash_ask.json`・`extra_ask`・外部送信ask検査)のみを無効化する。deny層(`bash_deny.json` + force-pushルール + `extra_deny`)は `enabled` の値に関わらず常に動作する。deny層を止める唯一の正規手段はHookを `hooks/hooks.json` から除去すること、または Claude Code 本体の `disableAllHooks` である。
- **クォート除去による過剰検知**: 判定前にコマンド文字列からクォート文字を除去するため、`echo 'rm -rf /'`(文字列リテラルとして安全なコマンド)も `rm-root-or-home` に一致し `deny` になり得る。誤検知よりも見逃しを避ける設計判断。
- **`rm` のフラグトークン上限**: `rm-recursive-or-force`(ask)と `rm-root-or-home`/`rm-home-root`(deny)は `(?:-\S+\s+){0,8}` でオプショントークンを最大8個までしか許容しない(ReDoS対策のトレードオフ)。フラグを9個以上並べて `-r`/`-f` を隠すコマンドは検出を回避できる。
- **変数展開は同一コマンド内の定数代入のみ**: `T=/; rm -rf $T` のように同一コマンド文字列内の単純な定数代入は展開してdeny判定できるが、`$(...)` によるコマンド置換や、コマンド外で `export` された環境変数のような動的な値は展開できない。`rm -rf $UNKNOWN` のようなコマンドはこのケースに該当し、deny判定には届かず ask(`rm-recursive-or-force`)止まりとなる。黙って許可しているわけではないが、deny層の決定論的ブロックはここには及ばない。
- **force-pushの保護は送信先refspec基準**: `force-push-refspec` はrefspecのコロン右側(送信先ブランチ)が `protected_branches` に一致する場合のみdenyにする。`git push origin +main:feature`(ローカルの `main` を保護対象外のリモートブランチへ送る操作)はこの基準では保護対象にならない。
- **SQL系ルールのクライアント文脈限定**: `sql-drop`/`sql-truncate` は `psql`/`mysql`/`mariadb`/`sqlite3`/`sqlcmd` の呼び出し文脈に限定したため、これら以外のSQLクライアント(未対応のCLIやGUIツール経由の実行など)による `DROP`/`TRUNCATE` は検出できない。
- **外部送信ask検査はcurl/wget限定**: `scp`/`rsync`/`nc` などの他の転送コマンドによる送信は対象外。
- **判定不能時は fail-close**: Hook自体が例外を送出した場合、ツール実行は止めず `ask` を返す(安全側)。
- deny/ask のいずれにも一致しなければ何も出力しないため、想定外の破壊的コマンドがルール未整備のまま素通りする可能性がある(新規パターンは [CONTRIBUTING.md](../../CONTRIBUTING.md) の手順で追加する)。
