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
- **設定ファイルからのdeny層解除不可(force-push保護を除く)**: `.claude-hooks.json` の `bash_guard.allow` は ask 層の判定のみを解除できる。`rules/bash_deny.json` の各ルール(`rm -rf /` 等の回復不能操作)および `secrets_guard` の保護パスを設定ファイルから解除する手段は用意されておらず、これらを止める唯一の方法はHook自体の無効化である。**例外**: force-push 保護だけは対象ブランチが設定可能で、`bash_guard.protected_branches` を空リスト `[]` にすると force-push の deny 規則自体が生成されなくなる(§4-7)。force-push の deny 規則は静的な `rules/bash_deny.json` ではなく、この設定から `bash_guard.py` 内で動的生成される。
- **`enabled: false` でもdeny層は解除できない**: `bash_guard.enabled: false` は ask 層(`rules/bash_ask.json`・`extra_ask`・curl/wget の外部送信ask検査)のみを無効化し、deny層の判定は継続する。`secrets_guard.enabled: false` に至ってはdeny層の無効化に一切効果がなく(no-op)、`systemMessage` で「enabled:false でもdeny層を無効化できません」と通知したうえで通常どおり検査を継続する。deny層を止める正規の手段は `hooks/hooks.json` からのHook除去、または Claude Code 本体の `disableAllHooks` のみである。
- **fail-closeによる安全側判定**: `bash_guard`/`secrets_guard` の判定処理中に例外が発生した場合、ツール実行を止めずに `ask` を返す(黙って通さない)。

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
9. **secrets_guard: write_protectedは正規表現+機械判定できる範囲のベストエフォート** — 書込保護の対象は、(a) Hook自身の設定ファイル(プロジェクトの `.claude-hooks.json`、グローバルの `~/.claude/claude-hooks.json`、および `.claude/` 配下の `settings.json`・`settings.local.json`)、(b) MCPサーバ定義・Claude Codeグローバル設定(`.mcp.json`・`.claude.json` — MCPサーバの `command` は任意コマンド実行経路になるため)、(c) このインストール自身の `hooks/`・`rules/` ディレクトリ、(d) 利用者が `secrets_guard.write_protected_paths` で追加したパス、である。これはエージェントが `enabled:false` を書き込む等で自らのガードを無力化する経路を塞ぐためのもの(#1A)。これらへの改変を、Edit/Writeの `file_path`、Bashコマンド中のシェル変異キーワード(リダイレクト `>`/`>>`(トークンに密着した `>file` を含む)、`dd of=`、`rm`/`mv`/`cp`/`sed -i`/`tee`/`truncate`/`ln`/`install`)、および `curl`/`wget` の出力フラグ(`-o`/`--output`/`-O`/`--output-document`/`--output-file`/`-a`/`--append-output` — ダウンロードによる設定ファイル上書き)を検査してdenyする。読取(`cat`・`grep`、`2>/dev/null` を伴う読取など)は妨げず、判定はリダイレクトの対象トークンや変異子・出力フラグの引数のみを保護対象と照合する(無関係なトークンでは誤denyしない)。ただしシェルの変異キーワードを一切使わないインタプリタレベルの書き込み(例: `python3 -c "open('.claude-hooks.json','w').write(...)"`)は、この検査を素通りする。§3で述べた「正規表現+機械判定できるものは確実に止め、それ以外はベストエフォート」という設計方針の一貫した帰結であり、write_protectedも例外ではない。この素通り経路によるClaude Code設定の変更は、[config_guard](hooks/config_guard.md)(`ConfigChange` 検知層)が事後に可視化する。詳細: [docs/hooks/secrets_guard.md](hooks/secrets_guard.md)。

## 5. fail-open / fail-close 方針

- **原則: fail-open + 可視化**。Hookスクリプト自体が例外を送出しても、ツール実行そのものは止めない(`exit 0`)。ただし `systemMessage` で「ガードが動作しなかった」ことを必ずユーザーへ通知する(`hook_io.fail_open`)。対象: `exfil_guard`、`exfil_output_scan`、`quality_gate`、`secrets_scan`、`config_guard`、`audit_log`(監査ログ書き込み失敗は無視して開発を止めない)。
- **例外: fail-close**。`bash_guard`・`secrets_guard` の **deny層判定中の例外のみ** は安全側に倒し、`ask` を返してユーザーの確認を求める(黙って通過させない)。
- **タイムアウト**: 各Hookは軽量に保つ方針で、`quality_gate` のみ長め(90秒、内部コマンドは45秒)のtimeoutを `hooks/hooks.json` で明示している。`exfil_guard` はsemantic判定(ヘッドレスClaude呼び出し、最大30秒)を含むため60秒。他のHookは概ね10〜15秒。
- **初回実行の必須ウォームアップ**: 各Hookは `uv run --script` シバンで動くため、そのマシンでの最初の実行時にPythonインタプリタの取得・インストールが発生し得る。この処理は上記のHookタイムアウト(概ね10秒)を超え得るため、導入直後に [README.md](../README.md)/[README.ja.md](../README.ja.md) の動作確認コマンドを一度実行し、実際のHook呼び出しより前にセットアップを完了させることを必須の手順として案内している。
- **監査ログ書き込み失敗は無視**する(開発を止めない、スペック セクション8)。

## 6. 監査ログの機微情報

`audit_log` は `tool_input` をJSON文字列化し**先頭500文字**を `tool_summary` として記録する。この500文字の中には、実行されたコマンドや編集内容の一部としてシークレット・PIIがそのまま残り得る。

- ログの出力先は既定で `.claude/logs/audit-YYYYMMDD.jsonl`(プロジェクトの `cwd` 起点の相対パス)。
- このパスは `.gitignore` により除外済みである(`logs/`、`.claude/logs/`、`*.jsonl`)。したがって**リポジトリへコミットされることは無い**が、**ローカルディスク上には機微情報を含み得るログファイルがそのまま残る**。ログの取り扱い(保存期間・アクセス権限・削除)は利用者側の運用に委ねられる。

関連: [docs/hooks/audit_log.md](hooks/audit_log.md)、[docs/configuration.md](configuration.md)。
