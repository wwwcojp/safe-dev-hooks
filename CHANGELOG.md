# Changelog

このプロジェクトの変更履歴は [Keep a Changelog](https://keepachangelog.com/ja/1.0.0/) の形式に従います。バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [0.8.0] - 2026-08-26

### Fixed
- **`audit_log` の `tool_summary` が、長い `tool_input` で高確率に壊れたJSONになっていた** — 旧実装は `json.dumps(tool_input)[:500]` と直列化後の文字列を単純スライスしていたため、切れ目が文字列リテラルの途中に落ちると `json.loads` できないレコードになっていた。実運用ログ(17日分・3プロジェクト)で計測したところ、Bashレコード13,968件中2,126件(15%)が該当し、すべて500文字ちょうどで頭打ちになっていた(原因はこの単純スライスのみと特定)。監査ログの目的(何が実行されたかの記録)は、パースできないレコードでは果たせない。
  `hooks/audit/audit_log.py` に `build_tool_summary()` を追加し、直列化する**前**に構造(値の長さ・キー/要素数・入れ子の深さ)を切り詰めるよう変更した。これにより `tool_summary` は常に妥当なJSON文字列になり(`SUMMARY_MAX_CHARS = 500` の予算は据え置き)、切り詰めが起きた箇所には `__audit_truncated__`/`__omitted_keys__`/`__omitted_items__`/値末尾の `…[+Nc]` タグを付与して、読者が「全文を見ている」と誤解しないようにした。マーカー形式の詳細は [docs/hooks/audit_log.md](docs/hooks/audit_log.md)。
  `tool_summary` は引き続きJSON文字列を格納する文字列フィールド(オンディスクの形は変更なし)。既存の(0.7.2以前の)ログファイルは遡って直さないため、壊れたレコードはそのまま残る。

### Changed(破壊的変更)
- **`quality_gate` の自動検出は、編集対象ファイルが承認済みプロジェクトの実境界内にある場合のみ実行するようになった** —
  `AUTO_DETECT`(`ruff check` / `rustfmt --check` / `npx --no-install eslint`)は、
  グローバル設定の `trusted_projects` にそのプロジェクトのエントリがある**だけ**では
  実行しない。編集対象ファイルが、承認済みプロジェクトルート(`root`)の**配下**
  (realpath 包含)であり、かつ `root` へ遡る途中に(`root` 自身を除き)別の `.git`
  境界が無いことも要求する(`_in_trusted_scope`)。**未承認のプロジェクトでは自動
  lint が走らなくなる**(通知が出る。既定 1 時間のクールダウン。通知は「承認していれば
  実際にコマンドが生成されたはずか」を副作用なしに判定してから出す — マーカー
  ファイルが無い等、承認しても何も変わらない場合は通知しない)。背景: これらは
  プロジェクト同梱の設定ファイルを読み込み、`eslint.config.js` は JavaScript として
  評価されるため、clone しただけの未承認リポジトリで `.js` を 1 ファイル編集すると
  リポジトリ由来のコードが実行され得た。0.7.0 の信頼ゲートは利用者が書いた
  `commands` を承認制にしたが、組み込みの `AUTO_DETECT` はその外側にあった。加えて
  「`root` が承認済みなら実行する」という初期実装には、承認済み `root` を足場に
  `root` 外の未承認ファイルを対象にする経路と、`CLAUDE_PROJECT_DIR` で承認済みの
  祖先へ `root` を持ち上げ自前の `.git` を持つ未承認のネストしたクローンを巻き込む
  経路の2つが実際に外部コマンドを起動できる状態で残っており、本リリース内の
  レビューで是正した。利用者が `commands` に明示したコマンドの扱いは変わらない。
  承認は `.claude-hooks.json` の有無と無関係で、ディレクトリ単位である。既知の限界:
  境界判定は `.git` の有無に依存するため、承認済みプロジェクト内の git submodule は
  自動検出も通知も出ない(無言)一方、`.git` を持たない未承認の vendored サブツリーは
  引き続き自動検出の対象になる。通知の状態管理は `hooks/lib/trust.py` の新セクション
  `autodetect_last`(状態ファイル `$HOME/.claude/safe-dev-hooks-state.json`)で、
  `skipped_last` とは枠を共有しない。`trusted_projects` に `false` で登録した
  プロジェクトにはこの通知を出さない。詳細:
  [docs/hooks/quality_gate.md](docs/hooks/quality_gate.md)、
  [docs/security-model.md](docs/security-model.md) §2・§4。

## [0.7.2] - 2026-08-25

### Fixed
- **`CLAUDE_PROJECT_DIR` の相対パスが、フックプロセスの作業ディレクトリ基準のまま採用されていた** — `_env_root` は `os.path.isdir(value)` でしか実在確認しておらず、値が相対パスの場合はフックプロセスの cwd 基準で解決されてから判定していた。`CLAUDE_PROJECT_DIR="."` や相対の `"pkg"` はそのまま `project_root` の戻り値として使われ、`.claude-hooks.json` の探索基準と `trust.project_key`(信頼承認のキー)の両方がフックプロセスの cwd に依存する状態が復活していた——0.7.1 がまさに解消したはずのプロセスcwd依存が、承認キー経由で再発していた。`".."` も、フックプロセスの cwd 基準の解決がたまたま event の `cwd` の実の祖先に一致すると祖先制約をすり抜けることを確認した。絶対パスであることを実在確認より前の採用条件に追加して修正。不採用にした値を拾って通知する `_rejected_env_dir` にも同じ process-cwd 依存があったため同様に修正した(相対値は「見つけた」と偽って通知しない)。
- **0.7.1 の記述の訂正** — 上記は 0.7.1 で新設した検証(`実在するディレクトリであり、かつ cwd の祖先であること`)自体に残っていた不具合であり、0.7.1 の CHANGELOG・`docs/configuration.md`・`_env_root` のdocstringはいずれも「相対パスは弾く」と記載していたが、0.7.1として出荷された実装はそうなっていなかった。0.7.1 のエントリ自体は当時の意図の記録として書き換えていない。

### Changed
- `docs/configuration.md`: `CLAUDE_PROJECT_DIR` の採用条件を3条件から4条件(空文字でない/絶対パスである/実在するディレクトリである/`cwd` の祖先である)に分割し、以降の条件番号の参照箇所を揃えた。

## [0.7.1] - 2026-08-24

### Fixed
- **作業ディレクトリがサブディレクトリのとき、プロジェクト設定が適用されず保護が無言で外れていた** — `.claude-hooks.json` の探索基準がイベントの `cwd`(Bashの `cd` に追従する一時的な作業ディレクトリ)のままだったため、Claudeがサブディレクトリへ移動した状態で発火したHookはプロジェクト設定を見つけられず、グローバル層+ビルトイン既定へ縮退していた(`secrets_guard.write_protected_paths` 等の保護が通知すら出さずに外れる)。基準を `hooks/lib/config.py` の新関数 `project_root(cwd)`(`CLAUDE_PROJECT_DIR` → cwdの最近傍のgitルート → cwd)に一本化して修正。
- 同じ原因で `.gitleaks.toml` の自動検出(`hooks/lib/scanners.py`)、`disableAllHooks` 警告(`config_guard`)、`quality_gate` のマーカー自動検出が、サブディレクトリで発火した場合に無言で効かなくなっていた問題を修正。
- 監査ログ(`audit_log`)の出力先が、相対 `path` 指定時にイベントの `cwd` 配下へ散らばっていた問題を修正。
- **設定に関する通知が実運用で一度も表示されていなかった問題を修正** — `audit_log`(SessionStart と全 PreToolUse/PostToolUse で走る最頻フック)が `load_config` を呼ぶ副作用で通知のクールダウンを記録しながら、通知そのものは表示せずに捨てていた。SessionStart が必ず最初に走るため、以後1時間はどの対話フックも同じ通知を出せず、0.7.1 で新設した「読まなかったプロジェクト設定」の通知も、**0.7.0 の「未承認のため無視しました」の通知も利用者に届いていなかった**。通知を表示しない呼び出しでは通知の生成そのものを行わないようにして修正(`load_config(..., notices=False)`)。採用/不承認の判定はこのフラグに依存しない。
- **`CLAUDE_PROJECT_DIR` が無検証で採用されていた問題を修正** — 空文字以外は、相対パス・存在しないディレクトリ・通常ファイルもそのまま採用され、静かに「プロジェクト設定なし」になっていた。加えて、リポジトリ同梱の `.claude/settings.json` の `env` でこの値を無関係なディレクトリへずらすと、サブディレクトリ作業中は通知が出ないまま本来のプロジェクト層が落ち、**利用者が別途承認済みの他プロジェクトの緩和設定**(`secrets_guard.allow_paths`・`bash_guard.allow`)を持ち込むこともできた。「実在するディレクトリであり、かつ `cwd` 自身または `cwd` の祖先であること」を採用条件にし、満たさなければ git 探索へフォールバックするよう修正。**(訂正: この修正には相対パスを弾く条件が実際には入っておらず、0.7.2 で修正。[0.7.2] を参照)**
- **落ちたプロジェクト層の通知条件を広げた** — 従来は「`cwd` 直下に `.claude-hooks.json` がある」ときだけ通知していたため、Claudeがサブディレクトリに居る典型的な状況(そこに設定ファイルは無い)では通知が出なかった。「`cwd` およびその祖先のうち、基準ディレクトリ以外で `.claude-hooks.json` を持つ場所」へ広げ、環境変数で基準を上位へずらされた場合と、ネストしたgitリポジトリ(vendored clone・submodule・worktree)で基準が下位へ移った場合の両方を可視化した。落ちた場所ごとに独立したクールダウンを持つ。
- **プロジェクト外へ `cd` するとプロジェクト層が無言で落ちていた** — `CLAUDE_PROJECT_DIR` の祖先制約(上記)は、Claudeが `cd /tmp` や `cd ~` を実行した後の `cwd` に対しても環境変数を不採用にする。このとき落ちる設定は `cwd` の祖先ではなく別の枝にあるため、上記の祖先まで広げた通知条件でも拾えず、承認済みの `write_protected_paths` 等が通知なしで外れていた。不採用にした場所に `.claude-hooks.json` があればそれも通知するようにして修正(採用はしないので祖先制約は緩まない — 可視化のみ)。

### Added
- 見つけたのに読まなかったプロジェクト設定(`.claude-hooks.json`)を通知するようになった。作業ディレクトリまたはその祖先に設定ファイルが存在するのに、基準ディレクトリ(プロジェクトルート)が別の場所に解決されたために読まなかった場合、`systemMessage` で通知する(場所ごとに独立した既定1時間のクールダウン、`notice_cooldown_sec` で調整。状態管理は `hooks/lib/trust.py` の新セクション `skipped_last`)。0.7.0の「無視した設定は必ず通知する」原則をこの経路にも適用したもの。

### Changed(挙動変更)
- **プロジェクト設定はプロジェクトルートのものだけを読むようになった** — モノレポでサブディレクトリに独自の `.claude-hooks.json` を置いている場合、従来はそこへ `cd` していれば読まれていたが、0.7.1からはプロジェクトルート直下の設定だけが読まれる(読まれなかった場合は上記のとおり通知される)。
- **`quality_gate` の実行ディレクトリもプロジェクトルートになった** — `commands` で明示設定したコマンドも自動検出コマンドも区別なく、プロジェクトルートを実行ディレクトリとして実行するようになった。`{file}` はEdit/Writeが常に絶対パスで渡すため置換結果は影響を受けないが、利用者が `commands` に書いた**相対パスを含む独自コマンド**は、従来の `cwd` ではなくプロジェクトルート基準で解決されるようになった点に注意。
- 既存の `trusted_projects` の承認は原則として再承認不要(基準の変更後もキーは解決後ルートの `realpath` で一致する)。**ただし** 次の2つの場合は再承認が必要になる。(1) モノレポでサブディレクトリの設定を承認していた場合、そのエントリは使われなくなるためルートの承認が別途必要。(2) **git worktree で作業する場合**、worktree ディレクトリ自身が `.git`(ファイル)を持つため基準はそのディレクトリになり、承認キーが本チェックアウトと別のパスになる(worktree ごとに承認が要る)。本リポジトリ自身が `CONTRIBUTING.md` で worktree 運用を案内しているので注意。
- `CLAUDE_PROJECT_DIR` は無条件ではなく検証を通った場合のみ採用されるようになった(空文字は未設定扱い、実在するディレクトリであること、`cwd` 自身または `cwd` の祖先であること)。`/add-dir` などでセッション開始ディレクトリの外を作業している場合はこの環境変数が不採用になり、実際に作業しているディレクトリの git ルートが基準になる。
- **既知の限界**: git リポジトリでない場所で `CLAUDE_PROJECT_DIR` も未設定の場合は、フォールバックとして従来どおり `cwd` 基準のままである。

## [0.7.0] - 2026-08-23

### Changed(破壊的変更)
- **プロジェクト直下の `.claude-hooks.json` は承認制になった。** グローバル設定 `$HOME/.claude/claude-hooks.json` の `trusted_projects` に、プロジェクトの `realpath` をキーとして内容ハッシュ(`"sha256:…"`、既定)/ `true`(ピン留めなし)/ `false`(明示的な不承認)を登録したプロジェクトのみマージする。未承認のプロジェクト設定は JSON として解析せず無視し、`systemMessage` に貼り付け可能な承認エントリを印字する(既定 1 時間のクールダウン、`notice_cooldown_sec` で調整)。**既存利用者のプロジェクト設定は承認するまで無効になる。** 背景: 敵対的リポジトリを clone して開くだけで deny 判定の緩和(`allow_paths`・`allow`・`trusted_servers`・`categories: "off"`・`protected_branches: []`)とコマンド実行(`quality_gate.commands`・`notify.command`・`scanners.*`)に到達できた(セキュリティスキャン 12 件の単一根本原因)。denylist 方式は列挙漏れで 2 度却下されたため、列挙そのものを廃止するオプトイン方式を採用。設計: `docs/superpowers/specs/2026-07-26-project-config-trust-optin-design.md`

### Added
- `hooks/lib/trust.py`: 承認判定・通知文面・状態ファイル(`$HOME/.claude/safe-dev-hooks-state.json`: 未承認通知のクールダウンとピン留めなし承認の変化検出)
- `hook_io.finalize` が `_notices`(意図的に採用しなかった設定の通知)を `_errors` と分けて合成。`audit_log` は通知を出さない
- `secrets_guard` の書込保護に `.claude/` 配下の `safe-dev-hooks-state.json` を追加。状態ファイルを先回りして書き換えると、ピン留めなし承認(`true`)での内容変化を知らせる通知を黙らせられるため(状態ファイルが読み書きできない場合は、可視性を優先して毎回通知する)

## [0.6.1] - 2026-08-23

### Fixed
- `secrets_guard` の write_protected 照合を「表記」でなく「イベントの `cwd` 基準で正規化した絶対パス」に対して行うよう修正。Bash の相対トークン(`echo x > .loop/state.json` 等)が `*/.loop/state.json` のようなパススコープ付きパターンを素通りしていた穴を塞ぐ(`~` 展開・`./`・`../` も同様。シンボリックリンクと `cd` またぎは追跡しない)。ビルトイン `rules/sensitive_paths.json` の相対表記の重複エントリ(`.claude/settings.json` / `.claude/settings.local.json`)は正規化で包含されるため削除。
- **`load_config` が例外を送出しなくなった(ガードの fail-open を防止)** — 不正UTF-8バイト列(`UnicodeDecodeError`)、再帰上限を超える深いネスト(`RecursionError`)、非dictの `exfil_guard.categories`(`AttributeError`)が捕捉漏れとなり、各Hookが判定前に異常終了していた。終了コードが2でないためツール実行は継続され、`bash_guard`/`secrets_guard` の deny 層と `config_guard` の通知がリポジトリ同梱の設定ファイル1つで無効化できる状態だった。読み込みの捕捉範囲を拡張し、`load_config` 全体を「例外を外に出さない」設計へ変更(異常時はビルトイン既定値 + `_errors`)。
- **設定の型不正時のフォールバック先を「直下の層」へ修正** — 従来は不正値をビルトイン既定値へ戻していたため、プロジェクト設定が `{"exfil_guard": 0}` のような型のすり替えを行うと、利用者が**グローバル設定で行った強化**(`mode: "always"`・`categories.pii: "deny"`・`protected_branches` 等)まで既定値へ戻せてしまった。検証を層ごとにマージ直後へ挟み、上位層の不正値は「その層をマージする前の状態」へ縮退させる。グローバル層自体の不正値がビルトイン既定へ戻る挙動は従来どおり。

### Changed
- 開発体制(利用者への影響なし): Loop Engineering 第1・2段階を導入 — ターン終了時の検証ゲート(`scripts/verify.py quick`: 実ホームパス漏洩チェック → ruff → pytest、loop-hooks プラグイン経由)と、mutmut によるファイル別 mutation score のラチェット(`scripts/verify.py mutation`、baseline は Git 追跡)。前提として hook/テストの import をルート起点(`from hooks.lib import …`)に統一(`hooks/__init__.py` 追加)。詳細: `CONTRIBUTING.md`、`docs/superpowers/specs/2026-08-22-loop-engineering-phase{1,2}*.md`。

## [0.6.0] - 2026-07-23

### Added
- 秘密検出の任意バックエンドとして gitleaks 委譲を追加(`scanners.gitleaks`: `auto`/`off`/`docker`)。内蔵 patterns を floor として残す union(加算)方式で、deny 保証を弱めずカバレッジを拡張。
- `scanners.gitleaks_image`(Docker イメージ)・`scanners.gitleaks_config`(`.gitleaks.toml` 指定、未指定時は `<cwd>/.gitleaks.toml` 自動)を追加。

### Changed
- `exfil_guard`/`secrets_scan`/`exfil_output_scan` の秘密検出を共有集約点 `scanners.scan_secrets` 経由に変更(内蔵挙動は不変・gitleaks 不在時は従来同等)。

## [0.5.0] - 2026-07-20

### Added

- **`config_guard`(新Hook / `ConfigChange`)** — セッション中の設定ファイル変更(user/project/local/policy/skills)を `systemMessage` で通知し、変更後に `disableAllHooks: true` が有効な場合は追加警告する検知専用フック。write_protected(予防層)が見えない経路 — インタプリタレベルの書込・外部プロセス・人間の手による編集 — での設定変更を可視化する。ブロックはしない(`disableAllHooks` という正規の解除手段、および人間自身の設定編集を妨げないため。warn→block 段階導入の原則)。設定キー `config_guard.enabled`(警告専用のため `false` で完全無効化可)。`audit_log` も `ConfigChange` に配線し監査ログへ記録する。

### Changed

- **`secrets_guard`: write_protected に `.mcp.json`・`.claude.json` を追加** — MCPサーバ定義の `command` は任意コマンド実行経路であり、フック定義(`settings.json`)と同格の改変標的となるため(Claude Code のプロジェクト設定ファイル群を攻撃面とした CVE-2025-59536 / CVE-2026-21852 の教訓。`docs/best-practices.md` セクション6.2)。
- **`secrets_guard`: `curl`/`wget` の出力フラグによる書込を write_protected の検査対象に追加** — `-o`(バンドル末尾 `-fsSLo`・密着引数 `-oFILE` を含む)/`--output`、wget の `-O`/`--output-document`(`=` 連結形式を含む)の引数トークンを保護対象と照合し、ダウンロードによる設定/フックファイルの上書きを deny する。wget の `-o`/`--output-file`(ログ書込)・`-a`/`--append-output`(ログ追記)も検査対象。セグメント内に curl/wget が混在する場合は両ツールのフラグ集合を適用。出力フラグの引数のみを照合するため、読取用途の `curl`(URL に保護ファイル名を含む場合など)や `/tmp` への保存は妨げない。`curl -O`・裸の `wget URL` のようにファイル名がURL側から決まる形式は対象外(既知の限界としてドキュメント化)。

## [0.4.0] - 2026-07-19

### Changed

- **`bash_guard`: force-push保護をrefspecまで拡張** — `bash_guard.protected_branches`(既定 `["main","master","develop","release","production"]`)を新設し、`--force`/`-f` 形式に加えて `+` refspec形式(例: `git push origin +HEAD:main`)も検出。refspecの送信先(コロン右側)が保護対象ブランチかどうかで判定するため、`git push origin +main:feature`(ローカルの`main`を保護対象外のリモートブランチへ送る操作)はdenyにならない。
- **`bash_guard`: `rm`/`find` のdeny降格対策** — `rm-root-or-home` に `rm -rf /.`・`rm -rf /..` のような末尾ドット回避を追加。新規 `find ... -delete` / `-exec rm` ルール(root/homeが対象なら `find-delete-root` でdeny、それ以外は `find-delete` でask)。同一コマンド内の定数代入(`T=/; rm -rf $T`)を展開してからdeny判定するようになった(動的な値は展開できず引き続きaskどまり)。
- **`bash_guard`: curl/wgetの機微データ送信をask検査** — データ送信フラグ(`-d`/`--data*`/`-F`/`--form`/`-T`/`--upload-file`等)と機微オペランド(環境変数、コマンド置換、`sensitive_paths.json` の保護ファイル名)を同時に含む `curl`/`wget` をaskへ倒す。`exfil_guard`(MCP/WebFetch/WebSearch専用)ではカバーされないbash経由の外部送信の隙間を埋める(`scp`/`rsync`/`nc` は対象外)。
- **`bash_guard`/`secrets_guard`: deny層の`enabled:false`免疫** — `bash_guard.enabled: false` はask層のみを無効化し、deny層は常に動作するよう修正(従来は無効化の余地があった)。`secrets_guard.enabled: false` はdeny層に対して完全にno-opとし、`systemMessage` で「enabled:false でも deny 層を無効化できません」と通知する。Hooksの完全無効化は `hooks/hooks.json` からの除去、または Claude Code 本体の `disableAllHooks` のみが正規の手段である。
- **`secrets_guard`: write_protectedで設定/フック自体の改変を遮断** — `.claude-hooks.json`・`settings.json`・`settings.local.json`・`hooks.json`、およびこのインストール自身の `hooks/`/`rules/` ディレクトリへの `Edit`/`Write`、および `Bash` 経由の変異コマンド(リダイレクト・`dd of=`・`rm`/`mv`/`cp`/`sed -i`/`tee`/`truncate`/`ln`/`install`)をdenyする。読取(`Read`)は妨げない。新設定キー `secrets_guard.write_protected_paths`。

### Docs

- `docs/security-model.md`/`docs/configuration.md`/`docs/hooks/bash_guard.md`/`docs/hooks/secrets_guard.md` を上記の変更に合わせて更新。特に `configuration.md` の「denyは`enabled:false`で外せる」という誤った記述を訂正。
- `CONTRIBUTING.md` にドッグフーディング時の注意(このリポジトリ自身のHooksを有効にしたまま `hooks/`/`rules/` を編集しようとするとwrite_protectedに遮断される旨)を追記。

### Fixed

- `bash_guard`: `protected_branches` に空リスト `[]` を明示した場合、従来は `["main","master"]` へ暗黙フォールバックしていたのを、「保護ブランチ無し」として force-push の deny 規則を生成しないよう修正(空の正規表現による全ブランチ誤検知も回避)。
- `bash_guard`: curl/wget 外部送信askの `allow` 照合を、ask層と同じくクォート除去後のセグメントに対して行うよう統一。
- `hook_io.finalize`: 設定エラー通知が既存の `systemMessage`(例: `secrets_guard` の enabled:false 注記)を上書きしていたのを、両者を連結して保持するよう修正。

## [0.3.0] - 2026-07-16

### Changed

- **破壊的変更** `notify`: デスクトップ通知(WSL→Windowsトースト / Linuxデスクトップ→notify-send / macOS→osascript)をHook本体へ統合し、既定動作をターミナルベルから自動判別(`notify.method: "auto"`)へ変更。デスクトップ通知が使えない環境では従来どおりベルへフォールバックする。ベルに固定したい場合は `notify.method: "bell"` を設定する。`notify.command` は従来どおり最優先で動作する(完全互換)。

### Removed

- **破壊的変更** `examples/notify_wrapper.sh`: 同等機能がHook本体へ統合されたため削除。`notify.command` に本スクリプトを絶対パスで指定していた場合、リポジトリ/プラグイン更新でスクリプトが消えるため、設定から `notify.command` を削除して既定の `auto` へ移行すること(同等以上の動作をする)。

## [0.2.0] - 2026-07-13

### Added

- `secrets_scan.custom_patterns`: 書き込み内容の検査にユーザー定義パターンを追加できる設定キー(`exfil_guard.custom_patterns` と同形式、ビルトインへマージ)。
- 実ホームパス混入防止の多層ガード: プロジェクト設定 `.claude-hooks.json`(`real-home-path` パターン)、プレースホルダー規約 `.claude/rules/no-personal-paths.md`、CIリークチェック(`ci.yml`)。

- `examples/notify_wrapper.sh`: `notify.command` に設定するデスクトップ通知ラッパー。実行環境を自動判別し、WSL(PowerShell/WinRTトースト)・Linuxデスクトップ(notify-send)・macOS(osascript)で通知、いずれも使えなければ `/dev/tty` へのベル出力(devcontainer等でも可聴)、制御端末も無ければ標準エラーへのベル出力にフォールバックする。

## [0.1.0] - 2026-07-05

### Added

- **Hooks 8本の初期実装**
  - `bash_guard`(PreToolUse / `Bash`): 破壊的コマンドのdeny/ask二段階ガード(`rules/bash_deny.json`・`rules/bash_ask.json`)
  - `secrets_guard`(PreToolUse / `Read|Edit|Write|Bash`): 機密ファイル(`.env`・秘密鍵・クラウド認証情報)への読取・編集・Bashアクセスの遮断(`rules/sensitive_paths.json`)
  - `exfil_guard`(PreToolUse / `mcp__.*|WebFetch|WebSearch`): MCP/Web外部送信引数のDLP検査(認証情報・PII・機密マーカー・カスタムパターン・semantic判定)
  - `exfil_output_scan`(PostToolUse / `mcp__.*|WebFetch|WebSearch`): MCP/Web応答からのシークレット・PII検出と警告/マスキング
  - `quality_gate`(PostToolUse / `Edit|Write`): 編集ファイルへのlint/format自動実行とClaudeへのフィードバック
  - `secrets_scan`(PostToolUse / `Edit|Write`): 書き込み内容からのシークレット検出とblock
  - `audit_log`(PreToolUse/PostToolUse/SessionStart/SessionEnd/Stop / `*`): 全イベントのJSONL非同期監査ログ
  - `notify`(Notification): 許可待ち・アイドル通知(ターミナルベル/カスタムコマンド)
- **設定システム**: `.claude-hooks.json`(プロジェクト)/ `~/.claude/claude-hooks.json`(グローバル)/ ビルトイン既定値の3層マージ、スキーマ検証と安全側フォールバック(`hooks/lib/config.py`)
- **データ駆動ルール定義**: `rules/bash_deny.json`、`rules/bash_ask.json`、`rules/sensitive_paths.json`、`rules/secret_patterns.json`、`rules/pii_patterns.json`、`rules/confidential_markers.json`、`rules/semantic_prompt.md`
- **配布**: `.claude-plugin/plugin.json` + `marketplace.json` によるプラグイン配布(`/plugin marketplace add` → `/plugin install safe-dev-hooks`)、`examples/settings.full.json` / `examples/settings.minimal.json` による手動導入スニペット
- **CI**: GitHub Actions で `ruff check` と `pytest` を実行(`.github/workflows/ci.yml`)
- **テスト**: 133件のpytestケース(危険系/グレー系/安全系、`&&`・`;`・`||` 連結やクォート・エスケープ等のバイパス試行、ReDoS回帰を含む)
- **ドキュメント**: README(日英)、Hookごとのリファレンス(`docs/hooks/*.md`)、設定リファレンス(`docs/configuration.md`)、セキュリティモデル(`docs/security-model.md`)、ベストプラクティス調査(`docs/best-practices.md`)、CONTRIBUTING.md

### Notes

- 実装・最終レビューにより設計時点からの変更がある(設計ドキュメントの決定事項ログ D12〜D16):
  - D12: `exfil_output_scan` のredactマスキングは1ルールにつき最大20件までの検出に限定(`MAX_FINDINGS_PER_RULE`)
  - D13: `secrets_guard` のBashトークン検査はパス形式のトークンのみを対象とし、裸のファイル名(拡張子なし)の直接アクセスは既知の限界として残っている
  - D14: `bash_guard` deny層の誤検知を除去(ホーム保護は `/home/<user>`・`/Users/<user>` 直下全体のみ、SQL系DROP/TRUNCATEはSQLクライアント実行文脈に限定)
  - D15: 設定のenum値(mode/action/categories.*)を検証し、不正値は安全側の既定値へフォールバック
  - D16: semantic判定のペイロード長ゲーティング(200文字未満スキップ)を撤廃し、長さに関わらず必ず判定(設定キー `min_payload_chars` は削除)
