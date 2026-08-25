# セキュリティモデル

## 1. 脅威モデル

このHooks集が対象とするのは **エージェント(Claude Code)の事故・暴走の防止** である。想定する失敗モードは、たとえば以下のようなものである。

- ユーザーの意図しない `rm -rf` や force push をエージェントが誤って実行してしまう
- エージェントが `.env` や秘密鍵を読み取り、その内容をコミットメッセージやMCPツールの引数に含めてしまう
- lint/formatを経ないままの編集が積み重なる
- MCPツールやWebFetch/WebSearchの応答に含まれるシークレット・PIIをエージェントが認識せずに別の場所へ転記してしまう

**このHooks集が対象としないもの**:

- **悪意あるユーザーへの防御**。ローカル環境で `claude` を操作できるユーザーは、Claude Codeの設定(`disableAllHooks`)や `settings.json` そのものを書き換えることで、任意のHookを無効化できる。悪意を持ってこれを行うユーザーからシステムを守る仕組みではない。
- **悪意あるプラグイン・MCPサーバーへの防御**。信頼できないプラグインやMCPサーバーを導入すること自体のリスクは、このHooks集の対象外である。

**対応プラットフォーム**: Linux(WSL2含む)とmacOSを対象とする。パス判定はPOSIX前提であり(ホーム保護は `/home/<user>`・`/Users/<user>`・`~`・`$HOME` を対象)、Windowsネイティブ環境(`C:\Users\...` 等のパス体系)は検証対象外。

## 2. 保証すること

- **deny層パターンの決定論的ブロック**: `bash_guard`/`secrets_guard` の deny 判定は正規表現による決定論的な照合であり、Claude Codeの permission mode(`acceptEdits`/`bypassPermissions` 等)に関わらず、Hookが有効である限り常に同じ結果でブロックされる。
- **設定ファイルからのdeny層解除不可(force-push保護を除く)**: 0.7.0 から、プロジェクト直下の `.claude-hooks.json`(リポジトリ由来の信頼できない入力)は、グローバル設定 `trusted_projects` で承認されない限り**一切マージされない**(JSON として解析もしない)。したがって未承認リポジトリを clone して開いただけでは、プロジェクト設定は deny 判定にもコマンド実行(`quality_gate.commands`・`notify.command`・`scanners.*`)にも影響しない。承認済み(内容ハッシュ一致、またはピン留めなし `true`)のプロジェクト設定は従来どおり最高優先度で適用され、`bash_guard.allow` は ask 層の判定のみを解除できる。`rules/bash_deny.json` の各ルール(`rm -rf /` 等の回復不能操作)および `secrets_guard` の保護パスを設定ファイルから解除する手段は用意されておらず、これらを止める唯一の方法はHook自体の無効化である。**例外**: force-push 保護だけは対象ブランチが設定可能で、`bash_guard.protected_branches` を空リスト `[]` にすると force-push の deny 規則自体が生成されなくなる(§4-7)。force-push の deny 規則は静的な `rules/bash_deny.json` ではなく、この設定から `bash_guard.py` 内で動的生成される。詳細: [docs/configuration.md](configuration.md) §1 信頼層。
- **プロジェクト層の型不正でグローバル層の強化が消えない**: 設定は層ごとにマージ直後に検証し、不正な値はその層をマージする前の状態(直下の層)へ戻す(0.6.1)。承認済みプロジェクトが `{"exfil_guard": 0}` のような型すり替えを行っても、グローバルで設定した `mode: "always"` や `categories.pii: "deny"` は保たれる。
- **`enabled: false` でもdeny層は解除できない**: `bash_guard.enabled: false` は ask 層(`rules/bash_ask.json`・`extra_ask`・curl/wget の外部送信ask検査)のみを無効化し、deny層の判定は継続する。`secrets_guard.enabled: false` に至ってはdeny層の無効化に一切効果がなく(no-op)、`systemMessage` で「enabled:false でもdeny層を無効化できません」と通知したうえで通常どおり検査を継続する。deny層を止める正規の手段は `hooks/hooks.json` からのHook除去、または Claude Code 本体の `disableAllHooks` のみである。
- **fail-closeによる安全側判定**: `bash_guard`/`secrets_guard` の判定処理中に例外が発生した場合、ツール実行を止めずに `ask` を返す(黙って通さない)。
- **gitleaksは加算(union)のみで内蔵floorを置換しない**: `secrets_scan`/`exfil_guard`(`categories.credentials`)/`exfil_output_scan` のシークレット検出は `hooks/lib/scanners.py` の `scan_secrets()` に集約されており、内蔵の `rules/secret_patterns.json` を常に無条件で先に走らせるfloorとしたうえで、任意バックエンドの `gitleaks`(`scanners.gitleaks`、既定 `"auto"`)は検出結果を上に加算するだけである。gitleaksが未導入・呼び出し失敗(タイムアウト・異常終了・JSON解析失敗)であっても、また利用者が緩い `.gitleaks.toml` allowlistを用意していても、内蔵floorの判定・`exfil_guard.categories.credentials=deny` の保証は一切弱まらない。詳細: [docs/hooks/secrets_scan.md](hooks/secrets_scan.md)、[docs/configuration.md](configuration.md)。
- **作業ディレクトリによってプロジェクト層の適用が変わらない(0.7.1)**: プロジェクト設定(`.claude-hooks.json`)の探索、`.gitleaks.toml` の自動検出、`config_guard` の `disableAllHooks` 検知、`quality_gate` のマーカー自動検出と実行ディレクトリ、`audit_log.path` の相対解決は、いずれもイベントの `cwd`(Bashの `cd` に追従する一時的な値)ではなく `hooks/lib/config.py` の `project_root(cwd)` が決定するプロジェクトルート(検証を通った `CLAUDE_PROJECT_DIR` → cwdの最近傍のgitルート → cwd)を基準にする。Claudeがサブディレクトリへ `cd` した状態でHookが発火しても、これらの判定はルートにいる場合と同じ結果になる。**例外はサブディレクトリ自身が別のgitリポジトリである場合**(vendored clone・submodule・`node_modules` 配下のリポジトリ)で、このときは `.git` の探索がそこで止まるためネストしたリポジトリがルートとして解決され、親プロジェクトの設定は読まれない。ただしこれは無言では起きない — 下記「無言で保護が外れない」の通知が出る。詳細: [docs/configuration.md](configuration.md)。
- **プロジェクト層が落ちたら無言では終わらない(0.7.1)**: 「見つけたのに読まなかった `.claude-hooks.json`」は必ず `systemMessage` で通知する。通知条件は「(1) `cwd` およびその祖先のうち、基準ディレクトリ以外で `.claude-hooks.json` を持つ場所」と「(2) 検証で不採用にした `CLAUDE_PROJECT_DIR` の場所(そこに `.claude-hooks.json` がある場合)」の2つ。`cwd` 直下に設定ファイルが無い(=サブディレクトリで作業している)場合も、環境変数で基準をずらされた場合も、ネストしたgitリポジトリで基準が下位へ移った場合も(1)で通知される。プロジェクト外へ `cd` して環境変数のアンカーが祖先制約で不採用になった場合は、落ちた設定が `cwd` の祖先ではなく別の枝にあるため(1)では拾えず、(2)で通知される。(2)は通知するだけで採用はしない — 祖先制約(別プロジェクトの承認済み設定の持ち込み防止)は緩めない。この通知を出さない `audit_log` は通知の生成自体を行わないため、クールダウンの枠を消費しない(0.7.1 以前は消費しており、実運用でこの通知も0.7.0の未承認通知も届いていなかった)。
- **未承認プロジェクトのコードを実行しない(0.8.0)**: `quality_gate` の自動検出は
  `trusted_projects` で承認済みのプロジェクトでのみ実行する。したがって未承認の
  リポジトリを clone して開いただけでは、`ruff`/`rustfmt`/`npx eslint` は起動せず、
  それらがリポジトリ同梱の設定(`eslint.config.js` は JavaScript として評価される)を
  読み込むこともない。承認済みプロジェクトでの実行は従来どおりであり、承認とは
  「このリポジトリのメンテナを信頼する」という表明である。

## 3. 保証しないこと

- **`disableAllHooks` やHook設定削除による無効化を防げない**: Claude Code の設定機能として、ユーザー(またはユーザーの操作を代行するエージェント自身)が `disableAllHooks` を設定する、または `settings.json`/プラグインの有効化状態を変更することで、Hooksを完全に迂回できる。これはClaude Code本体の仕様であり、本Hooks集の実装では防げない。ただし [config_guard](hooks/config_guard.md)(`ConfigChange` イベントの検知層)が、セッション中の設定変更の発生と `disableAllHooks` の有効化を `systemMessage` でユーザーへ通知する — 防止はできないが、黙って無効化されることはない(通知の直後までは有効なため)。
- **正規表現(パターン)の網羅性**: `bash_guard`/`secrets_guard`/`exfil_guard`/`secrets_scan`/`exfil_output_scan` の検出は、データ駆動の正規表現ルール(`rules/*.json`)を中核に、それに準じるコード側の機械的な照合(force-push の保護ブランチ生成・自ディレクトリ判定・同一コマンド内の変数展開など)を組み合わせて行う。いずれも決定論的なパターン照合であり、未知の攻撃・難読化・新しいツールのコマンド体系など、ルール/パターンに存在しないものは検出できない。
- **semantic判定の確率性(検出漏れあり)**: `exfil_guard` の semantic カテゴリはヘッドレスClaude(`claude -p`)による確率的な判定であり、`ask` 専用(`deny` には昇格しない)。LLMの判定ミス・タイムアウト・`claude` CLI不在時のフォールバック(自動スキップ)により、機微情報が検出されずに通過する場合がある。
- **文脈依存PII(人名等)の完全検出**: メールアドレスやクレジットカード番号のような形式的パターンは正規表現+バリデータ(Luhn・マイナンバーのチェックデジット)で機械的に検出できるが、人名・所属・肩書きのように文脈でしか機微性が判断できない情報は正規表現では検出できない。semanticカテゴリがベストエフォートで補完するが、上記のとおり確率的であり完全ではない。

保証レベルのまとめ: **正規表現+組織定義パターンで機械的に判定可能なものは確実に止め、それ以外はsemanticでベストエフォート検出する**、というのが本Hooks集の一貫した設計方針である。

## 4. 既知の実装上の限界(実装レビューで判明した事項)

以下は実装時のレビューで明らかになった、個別Hookの具体的な検出漏れ・過剰検知である。詳細は各Hookのリファレンスにも記載している。

1. **secrets_guard: 裸のファイル名(拡張子なし)のBash直接アクセスは検知漏れとなる(D13)** — `secrets_guard` のBashトークン検査は「パス形式のトークン」のみを対象にしている(`/` を含む、`.`/`~` で始まる、または `.` を含むトークンのみ)。これは `grep credentials` や `find -name "*.pem"` のような検索コマンドまで解除不能denyにしてしまうと実用性を損なうためのトレードオフである。結果として、`cat credentials`(パス区切り・ドット・チルダを一切含まない裸のファイル名)のような直接アクセスは検査対象から外れ、検知漏れとなる。一方 `~/.aws/credentials` のようなパス形式であれば捕捉される。詳細: [docs/hooks/secrets_guard.md](hooks/secrets_guard.md)。
2. **exfil_output_scan: redactマスキングは1ルールにつき20件まで(D12)** — `scan_text` は同一ルール内で重複しない完全一致文字列を最大20件(`MAX_FINDINGS_PER_RULE`)まで収集する。1つの応答内に同一ルールで21件目以降の異なるシークレット・PIIが含まれる場合、それらはマスキングされずに応答へ残る。詳細: [docs/hooks/exfil_output_scan.md](hooks/exfil_output_scan.md)。
3. **bash_guard: `rm` のフラグトークンが9個以上あると検知漏れとなる** — `rm-recursive-or-force`(ask)・`rm-root-or-home`(deny)の正規表現はReDoS対策として `(?:-\S+\s+){0,8}` でオプショントークンを最大8個までしか許容していない。フラグを9個以上並べて `-r`/`-f` を隠すコマンドは、この上限を超えるため検出を回避できる。詳細: [docs/hooks/bash_guard.md](hooks/bash_guard.md)。
4. **bash_guard: クォート除去により文字列リテラルも過剰検知される** — 判定前にコマンド文字列からクォート文字(`"`/`'`)を除去してから照合するため、`echo 'rm -rf /'` のような、実行内容としては無害な文字列リテラルを含むコマンドも `rm-root-or-home` に一致し `deny` になり得る。検知漏れよりも誤検知を許容する設計判断である。詳細: [docs/hooks/bash_guard.md](hooks/bash_guard.md)。
5. **exfil_guard: semantic判定は確率的でありask専用・fail-open** — ヘッドレスClaude呼び出しによる判定であるため検出漏れ・誤判定があり得る。`deny` には使わず `ask` にのみ変換する。`claude` CLIが `PATH` 上に無い環境では自動的にスキップされ、正規表現ベースの他カテゴリのみで動作を継続する(判定不能を理由にツール実行を止めることはない)。詳細: [docs/hooks/exfil_guard.md](hooks/exfil_guard.md)。
6. **bash_guard: 変数間接化は同一コマンド内の定数代入のみ展開できる** — `T=/; rm -rf $T` のように、同一コマンド文字列内で `VAR=定数値` の代入がある場合はそれを展開したうえでdeny判定する。しかし `$(...)` によるコマンド置換や、コマンド実行前に別途 `export` されている環境変数のように、コマンド文字列単体からは値が読み取れない動的な値は展開できない。この場合 `rm -rf $UNKNOWN` はdeny判定に届かず `ask`(`rm-recursive-or-force`)止まりとなる。黙って許可しているわけではないが、deny層の決定論的ブロックはこのケースには及ばない。詳細: [docs/hooks/bash_guard.md](hooks/bash_guard.md)。
7. **bash_guard: force-pushの保護はrefspecの送信先ブランチ名に対して判定する** — `force-push-refspec` ルールは `git push origin +HEAD:main` のような `+` 付きrefspecを、コロンの右側(送信先ブランチ)が `bash_guard.protected_branches`(既定 `main`/`master`/`develop`/`release`/`production`)に一致する場合のみdenyにする。したがって `git push origin +main:feature`(ローカルの `main` を保護対象外のリモートブランチへ送る操作)はdenyにならない。保護対象はプッシュ「先」であって、ローカル側のブランチ名ではない。なお `protected_branches` を空リスト `[]` にすると force-push の deny 規則自体が生成されず、`--force`/`+`refspec のいずれも deny されなくなる(ask 層の `git-force-push` により、`--force`/`-f` を伴うものは引き続き ask になる)。この「設定による deny 無効化」は force-push 保護に固有であり、他の deny ルールには当てはまらない(§2)。
8. **bash_guard: bash経由の外部送信askはcurl/wgetのみ対象** — `curl`/`wget` がデータ送信フラグ(`-d`/`--data*`/`-F`/`--form`/`-T`/`--upload-file`/`--post-data`/`--post-file`/`--body-data`/`--body-file`)と機微オペランド(環境変数参照、コマンド置換、または `sensitive_paths.json` の保護ファイル名)を同時に含む場合に `ask` へ倒す。`exfil_guard`(MCP/WebFetch/WebSearch専用)ではカバーされないbash経由の外部送信の隙間を埋めるものだが、`scp`/`rsync`/`nc` など他の転送コマンドは対象外であり、`deny` に昇格することもない。
9. **scanners: `gitleaks: "docker"` はリモート`DOCKER_HOST`環境で検査対象ペイロードを外部送信し得る** — `scanners.gitleaks` を `"docker"` に設定すると `hooks/lib/scanners.py` は `docker run` にstdin経由で検査対象テキストを渡す。`DOCKER_HOST` 環境変数がリモートのDockerデーモンを指している場合、そのペイロードは実行中のマシン外(リモートdaemon)へ送信され得る。本設定はローカルdaemon前提であり、リモート `DOCKER_HOST` を使う環境で秘密情報を含み得るペイロードを扱う場合は明示的なリスクとして認識すること。既定の `"auto"`(gitleaksバイナリ直接実行、PATH上に無ければ無コストでスキップ)ではこの経路は発生しない。詳細: [docs/configuration.md](configuration.md)。
10. **secrets_guard: write_protectedは正規表現+機械判定できる範囲のベストエフォート** — 書込保護の対象は、(a) Hook自身の設定ファイル(プロジェクトの `.claude-hooks.json`、グローバルの `~/.claude/claude-hooks.json`、および `.claude/` 配下の `settings.json`・`settings.local.json`)、(b) MCPサーバ定義・Claude Codeグローバル設定(`.mcp.json`・`.claude.json` — MCPサーバの `command` は任意コマンド実行経路になるため)、(c) 信頼判定の通知状態ファイル(`.claude/` 配下の `safe-dev-hooks-state.json` — 先回りして書き換えることでピン留めなし承認の内容変化通知を黙らせる経路を塞ぐ。0.7.0)、(d) このインストール自身の `hooks/`・`rules/` ディレクトリ、(e) 利用者が `secrets_guard.write_protected_paths` で追加したパス、である。これはエージェントが `enabled:false` を書き込む等で自らのガードを無力化する経路を塞ぐためのもの(#1A)。これらへの改変を、Edit/Writeの `file_path`、Bashコマンド中のシェル変異キーワード(リダイレクト `>`/`>>`(トークンに密着した `>file` を含む)、`dd of=`、`rm`/`mv`/`cp`/`sed -i`/`tee`/`truncate`/`ln`/`install`)、および `curl`/`wget` の出力フラグ(`-o`/`--output`/`-O`/`--output-document`/`--output-file`/`-a`/`--append-output` — ダウンロードによる設定ファイル上書き)を検査してdenyする。読取(`cat`・`grep`、`2>/dev/null` を伴う読取など)は妨げず、判定はリダイレクトの対象トークンや変異子・出力フラグの引数のみを保護対象と照合する(無関係なトークンでは誤denyしない)。ただしシェルの変異キーワードを一切使わないインタプリタレベルの書き込み(例: `python3 -c "open('.claude-hooks.json','w').write(...)"`)は、この検査を素通りする。§3で述べた「正規表現+機械判定できるものは確実に止め、それ以外はベストエフォート」という設計方針の一貫した帰結であり、write_protectedも例外ではない。この素通り経路によるClaude Code設定の変更は、[config_guard](hooks/config_guard.md)(`ConfigChange` 検知層)が事後に可視化する。なお Bash トークンと `file_path` はイベントの `cwd` を基準に絶対パスへ正規化してから照合する(相対・`./`・`../`・`~`・絶対のどの表記でも同じ判定)が、コマンド内の `cd` をまたぐ相対パスとシンボリックリンク経由の別名は追跡しない。詳細: [docs/hooks/secrets_guard.md](hooks/secrets_guard.md)。
11. **パスの同一性判定と書込先の正規化は別物** — `trusted_projects` のキーは `os.path.realpath(cwd)`(シンボリックリンク解決済みの同一性)で照合する。一方 `secrets_guard` の write_protected の**パターン照合**は、Bash トークン/`file_path` をイベントの `cwd` 基準で絶対化するだけでシンボリックリンクを解決しない(利用者が指定した「書こうとしている場所」の表記を見る)。前者は「同じプロジェクトか」、後者は「保護対象に書こうとしているか」という異なる問いに答えており、意図的に揃えていない。なお同じ関数内でも、自インストール配下(`hooks/`・`rules/`)の判定だけは `resolve()` 済みの絶対パスで包含関係を見る — こちらは「同一性」の問いだからである。**`secrets_guard` の書込先正規化(`_normalize_target`)は 0.7.1 の `project_root(cwd)` 導入後も引き続き `cwd` 基準のまま変更していない** — こちらは「利用者がどこに書こうとしているか」という、プロジェクト設定の探索基準(「どのプロジェクトの設定を読むか」)とは別の問いに答えているため(意図的な不変更。上記「触ってはいけない箇所」の設計判断)。
12. **D3: プロジェクトルートのアンカーが環境変数由来であることの影響(0.7.1、絶対パス条件は0.7.2)** — `project_root(cwd)` は `CLAUDE_PROJECT_DIR` 環境変数を最優先で採用する。この値はハーネス(Claude Code)がHook実行時に注入するものであり、リポジトリ同梱の `.claude/settings.json` の `env` 設定に影響され得る。もっとも `trust.gate()` は解決後パスの `realpath` をキーに `trusted_projects` の承認を要求するため、この経路で任意のパスを自己承認させることはできない(未承認のまま採用されることはない)。残る影響と、それぞれへの対処は次のとおり。
    - **本来読まれるべきプロジェクト設定を読ませなくする** — 可能。ただし「見つけたのに読まなかった設定」の通知(D2、`hooks/lib/trust.py` の `notify_skipped`)により必ず可視化される。通知条件は `cwd` 直下だけでなく**その祖先まで**を対象にするため、Claudeがサブディレクトリで作業していて `cwd` 側に設定ファイルが無い状況でも通知される。
    - **利用者が別途承認済みの他プロジェクトの緩和設定を持ち込む**(`secrets_guard.allow_paths` は deny より先に評価される。`bash_guard.allow` は ask/exfil-ask を抑止する)— **できない**。`CLAUDE_PROJECT_DIR` は絶対パスであり、かつ `cwd` 自身または `cwd` の祖先を指す実在のディレクトリでなければ採用されず(`hooks/lib/config.py` の `_env_root`)、無関係な別プロジェクトを指す値は不採用になって git 探索へフォールバックする。絶対パス条件(0.7.2)が要る理由: 相対値はハーネスが注入した値ではなくフックプロセスの作業ディレクトリを基準に解決されてしまい、これはまさに0.7.1が除こうとしたプロセスcwd依存そのものであるうえ、この環境変数自体がリポジトリ同梱の `.claude/settings.json` の `env` から差し替え可能(攻撃者が影響できる入力)なので、実在確認や祖先制約より前に弾く必要がある。
    - git リポジトリでなく `CLAUDE_PROJECT_DIR` も無い環境では `cwd` へフォールバックし、この経路自体が発生しない。
13. **自動検出は 0.7.0 の信頼ゲートの外側にあった**: 0.7.0 は利用者が書いた
    `quality_gate.commands` を承認制にしたが、組み込みの `AUTO_DETECT` はゲートを
    通らなかった。さらに 0.7.1 でマーカー探索の基準がプロジェクトルートになった結果、
    サブディレクトリ作業中にも発火するようになり露出が広がった。0.8.0 で承認制に統一した。

## 5. fail-open / fail-close 方針

- **原則: fail-open + 可視化**。Hookスクリプト自体が例外を送出しても、ツール実行そのものは止めない(`exit 0`)。ただし `systemMessage` で「ガードが動作しなかった」ことを必ずユーザーへ通知する(`hook_io.fail_open`)。対象: `exfil_guard`、`exfil_output_scan`、`quality_gate`、`secrets_scan`、`config_guard`、`audit_log`(監査ログ書き込み失敗は無視して開発を止めない)。
- **例外: fail-close**。`bash_guard`・`secrets_guard` の **deny層判定中の例外のみ** は安全側に倒し、`ask` を返してユーザーの確認を求める(黙って通過させない)。
- **プロジェクト設定の信頼判定も fail-close**(0.7.0)。`hooks/lib/trust.py` の `gate()` は内部で例外を出さない設計だが、境界の `try/except` が最後の砦として万一の異常を捕捉し「不採用」へ倒す(採用側へは倒さない)。`systemMessage` には `safe-dev-hooks: プロジェクト設定の信頼判定に失敗したため無視しました: <例外の型>: <メッセージ>` を印字して可視化する。
- **タイムアウト**: 各Hookは軽量に保つ方針で、`quality_gate` のみ長め(90秒、内部コマンドは45秒)のtimeoutを `hooks/hooks.json` で明示している。`exfil_guard` はsemantic判定(ヘッドレスClaude呼び出し、最大30秒)を含むため60秒。他のHookは概ね10〜15秒。
- **初回実行の必須ウォームアップ**: 各Hookは `uv run --script` シバンで動くため、そのマシンでの最初の実行時にPythonインタプリタの取得・インストールが発生し得る。この処理は上記のHookタイムアウト(概ね10秒)を超え得るため、導入直後に [README.md](../README.md)/[README.ja.md](../README.ja.md) の動作確認コマンドを一度実行し、実際のHook呼び出しより前にセットアップを完了させることを必須の手順として案内している。
- **監査ログ書き込み失敗は無視**する(開発を止めない、スペック セクション8)。

## 6. 監査ログの機微情報

`audit_log` は `tool_input` を構造保存トランケーション([docs/hooks/audit_log.md](hooks/audit_log.md)参照)で `SUMMARY_MAX_CHARS`(500文字)以内に切り詰め、常に妥当なJSONとして `tool_summary` に記録する。この記録範囲には、実行されたコマンドや編集内容の一部としてシークレット・PIIがそのまま残り得る。

- ログの出力先は既定で `.claude/logs/audit-YYYYMMDD.jsonl`(プロジェクトルート起点の相対パス。0.7.1。基準の決定順序は[docs/configuration.md](configuration.md)参照)。
- このパスは `.gitignore` により除外済みである(`logs/`、`.claude/logs/`、`*.jsonl`)。したがって**リポジトリへコミットされることは無い**が、**ローカルディスク上には機微情報を含み得るログファイルがそのまま残る**。ログの取り扱い(保存期間・アクセス権限・削除)は利用者側の運用に委ねられる。

関連: [docs/hooks/audit_log.md](hooks/audit_log.md)、[docs/configuration.md](configuration.md)。
