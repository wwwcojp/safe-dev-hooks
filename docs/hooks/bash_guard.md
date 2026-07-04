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

### deny になる条件(`rules/bash_deny.json`。設定で解除不可)

| ルール名 | 意味 |
|---|---|
| `rm-root-or-home` | `/`・`~`・`$HOME` を対象にした `rm`(フラグの有無に関わらず) |
| `rm-system-dir` | `/etc` `/usr` `/var` `/bin` `/boot` `/lib` `/home` `/opt` `/srv` を対象にした `rm` |
| `sudo-rm` | `sudo rm` |
| `force-push-protected` / `force-push-protected-order` | `main`/`master` ブランチへの force push(引数順の入れ替えにも対応) |
| `mkfs` | `mkfs` 系コマンド |
| `dd-to-device` | `dd ... of=/dev/...` |
| `fork-bomb` | `:(){ :\|:& };:` 形式のfork bomb |
| `chmod-777-root` | `chmod 777 /` |
| `redirect-to-device` | `/dev/sd[a-z]` への出力リダイレクト |
| `sql-drop` | `DROP TABLE` / `DROP DATABASE`(大文字小文字を無視) |
| `sql-truncate` | `TRUNCATE TABLE`(大文字小文字を無視) |

加えて `.claude-hooks.json` の `bash_guard.extra_deny`(正規表現の配列)がビルトインdenyルールへマージされる。

### ask になる条件(`rules/bash_ask.json`)

| ルール名 | 意味 |
|---|---|
| `git-reset-hard` | `git reset --hard` |
| `git-clean-force` | `git clean -f` 系 |
| `git-force-push` | `main`/`master` 以外への force push |
| `rm-recursive-or-force` | `-r`/`-f` を含む `rm`(フラグ最大8個までの間に挟まれたオプションを許容。[既知の限界](#既知の限界)参照) |
| `pipe-to-shell` | `curl`/`wget` の出力を `sh`/`bash`/`zsh`/`dash` にパイプ |
| `npm-publish` | `npm publish` |
| `git-discard-worktree` | `git checkout .` / `git restore .` |

`.claude-hooks.json` の `bash_guard.extra_ask` がビルトインaskルールへマージされる。`bash_guard.allow`(正規表現の配列)に一致するセグメントは ask 判定から除外される(**ask層のみ解除可能。deny層は解除不可**)。

### 何もしない条件

どのdeny/askルールにも一致しないコマンド。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `bash_guard.enabled` | `true` | falseで本Hookを無効化 |
| `bash_guard.extra_deny` | `[]` | 追加のdeny正規表現(ビルトインへマージ、解除不可) |
| `bash_guard.extra_ask` | `[]` | 追加のask正規表現(ビルトインへマージ) |
| `bash_guard.allow` | `[]` | ask層の判定から除外する正規表現(deny層には無効) |

## 既知の限界

- **クォート除去による過剰検知**: 判定前にコマンド文字列からクォート文字を除去するため、`echo 'rm -rf /'`(文字列リテラルとして安全なコマンド)も `rm-root-or-home` に一致し `deny` になり得る。誤検知よりも見逃しを避ける設計判断。
- **`rm` のフラグトークン上限**: `rm-recursive-or-force`(ask)と `rm-root-or-home`(deny)は `(?:-\S+\s+){0,8}` でオプショントークンを最大8個までしか許容しない(ReDoS対策のトレードオフ)。フラグを9個以上並べて `-r`/`-f` を隠すコマンドは検出を回避できる。
- **判定不能時は fail-close**: Hook自体が例外を送出した場合、ツール実行は止めず `ask` を返す(安全側)。
- deny/ask のいずれにも一致しなければ何も出力しないため、想定外の破壊的コマンドがルール未整備のまま素通りする可能性がある(新規パターンは [CONTRIBUTING.md](../../CONTRIBUTING.md) の手順で追加する)。
