# Mutation スパイク結果(Loop Engineering 第2段階の入口)

実施日: 2026-08-22
設計書: `2026-08-22-loop-engineering-phase2-mutation-design.md`
実施場所: リポジトリの使い捨てコピー(scratch)。本体は未変更

---

## 結論: mutmut 3.7.0 を採用。import のルート起点化(`from hooks.lib import …`)が前提

news-collector のスパイク(`~/news-collector/docs/superpowers/specs/2026-08-20-mutation-spike-results.md`)と
同じ結論・同じ前提。cosmic-ray・自作ミューテータの検証は不要。

## 経過

1. **現レイアウトのまま**(`source_paths=["hooks"]`, `only_mutate=["hooks/lib/patterns.py"]`)→ 早期停止:

   > Recorded keys (e.g.): `['lib.patterns.x_luhn_ok', …]`
   > Expected keys (e.g.): `['hooks.lib.patterns.x_luhn_ok', …]`

   原因: mutmut は変異キーを**ファイルパス由来**(`hooks.lib.patterns`)で期待するが、このリポジトリは
   conftest が `hooks/` を `sys.path` に注入し、実行時モジュール名が `lib.patterns` になる。
   mutmut にプレフィックス変換の設定は無い(`src/`・`source/` の剥離のみ特別扱い)。
2. **`paths_to_mutate` に単一ファイルを指定**すると `mutants/hooks/lib/` に他の lib が
   コピーされず import で落ちる → `source_paths` はディレクトリ(`hooks`)、絞り込みは `only_mutate` で行う。
3. **`also_copy` が要る**: テストが読むリポジトリファイル(`rules/`・`examples/`・`.github/`・`docs/`・
   README・CONTRIBUTING・`.claude-plugin/`・`scripts/`)を `mutants/` に複製しないと
   `test_packaging` / `test_verify` が落ちる。
4. **import をルート起点に揃えたコピーで完走**:
   - hook スクリプト 9 本: `sys.path.insert(0, <plugin root>)` + `from hooks.lib import …`
   - テスト 9 本: `from hooks.lib import …`、conftest は `REPO_ROOT` を挿入
   - `tests/helpers.load_hook`: `importlib.import_module("hooks.<dir>.<name>")` 方式
     (`spec_from_file_location` のままだと `bash_guard.x_…` で記録され、ガード本体が対象にできない)
   - pytest 239 件 green、hook の直接実行(`uv run hooks/pre_tool_use/bash_guard.py` に stdin)exit 0

## 実測(lib 4 本 + bash_guard、803 変異、**約 11 秒**、122 mutations/sec)

| ファイル | 変異 | killed | survived | no tests | score |
|---|---|---|---|---|---|
| `hooks/lib/config.py` | 161 | 124 | 37 | 0 | 77.0 |
| `hooks/lib/hook_io.py` | 79 | 64 | 15 | 0 | 81.0 |
| `hooks/lib/patterns.py` | 112 | 93 | 19 | 0 | 83.0 |
| `hooks/lib/scanners.py` | 148 | 129 | 19 | 0 | 87.2 |
| `hooks/pre_tool_use/bash_guard.py` | 303 | 201 | 40 | 62 | 66.3 |

score = killed / 全変異(「no tests」= その関数を呼ぶテストが無い、は未検出扱い)。
mutmut の終了コード→状態: `1/3/-24`=killed、`0`=survived、`5/33`=no tests、`24/152/255`=timeout、
`34`=skipped、`35`=suspicious、`37`=caught by type check(`mutmut/__main__.py` の `status_by_exit_code`)。

生き残りのサンプル(bash_guard):

- 変数境界の regex `(?![A-Za-z0-9_])` → `(?![a-za-z0-9_])` が生き残る = **大文字に続く `$VAR` 境界がテストされていない**(本物の穴)
- `text.replace("'", "")` → `replace("XX'XX", "")` が生き残る = クォート除去の効果を直接見るテストが無い
- `rstrip("/")` → `rstrip(None)` が生き残る = 末尾スラッシュ付きパスの basename 判定が未テスト

**ガード本体の生き残りは deny 層の検出漏れに直結しうる**ため、lib と同等以上に価値がある。

## トリアージの結果(2026-08-22 追記)

| ファイル | 変異 | 初回 score | 最終 score | 補強したテスト(パターン) | pragma(理由) | 残り(正体) |
|---|---|---|---|---|---|---|
| `hooks/lib/patterns.py` | 112 | 83.0 | 95.5 | `luhn_ok` の先頭ガード(非数字+境界長で例外を出さず False を返すか、`or`/`and` 境界)、`<13`/`<=13`/`<14` の長さ境界と 2 倍化・9 超補正・total 加算を貫通する 13 桁有効値、`mynumber_ok` の `rem<=1` 境界(rem=1, rem=2 の実値)、`scan_text` の `continue`(バリデータ不通過時は次のマッチへ)と `break`(このルールだけ打ち切り、次のルールは継続)の違い、`MAX_FINDINGS_PER_RULE` の `>=` 境界 | 0(下記5件は理由を確認したが、同一行に既存の実変異(killed)が同居しており pragma すると mutmut がその行自体を変異対象から外してしまうため、あえて付けず表に記録するに留めた) | 5件: `x_luhn_ok__mutmut_19`(`if d > 9:` → `d >= 9`。2倍化直後の `d` は常に偶数〈0,2,…,18〉で奇数の9を取り得ないため `>9` と `>=9` は全入力で同一集合を弾く真の等価変異。500,000件のランダム入力で不一致0件を確認)/`x_luhn_ok__mutmut_25`(`total += d` → `total -= d`。最終判定が `total % 10 == 0` のみで、`S%10==0 ⟺ (-S)%10==0` が任意の整数で成立するため符号反転は判定に無関係な真の等価変異。同じくランダム検証で不一致0件)/`x_scan_text__mutmut_13,15,18`(`rule.get("validator", "")` のデフォルト値を `None`/省略/`"XXXX"` に変える3種。`"validator"` キーが無いルールでは `_VALIDATORS.get(default)` を呼ぶが、`_VALIDATORS` のキーは `"luhn"`/`"mynumber"` のみで、"", None, "XXXX" のいずれも該当せず結果は常に `None` = 真の等価変異。ただし同じ行には `.get("validator", …)` の**キー**を壊す変異〈`None`・`""`・`"XXvalidatorXX"`・`"VALIDATOR"` など〉も同居しており、これらは既存テストで killed 済みの実変異なので pragma で行ごと除外しない) |
| `hooks/lib/hook_io.py` | 79 | 81.0 | 98.7 | `emit` の非ASCIIエスケープ抑止を capsys の完全一致で固定(`json.dumps(..., ensure_ascii=False)` と厳密比較し `\u` エスケープが残っていないことも確認)、`post_block` の `context` 省略時デフォルト値(`hookSpecificOutput` キー自体が省かれること)と `hookEventName` キー/値を辞書の完全一致で固定、`finalize` の設定エラーmessageのプレフィックス文言・`"; "` 区切りを複数エラーで完全一致固定、既存テストが皆無だった `fail_open` に exit code(`== 0`)と `systemMessage` 全文の完全一致テストを新規追加 | 0(下記1件は等価変異と確認したが、同じ行に実変異〈`ensure_ascii=True` への変異・`ensure_ascii` kwarg 省略〉が同居しており、pragma を付けると mutmut が行ごと変異対象から除外してテスト済みの実変異まで見逃すため、あえて付けず表に記録するに留めた) | 1件: `x_emit__mutmut_3`(`json.dump(obj, sys.stdout, ensure_ascii=False)` の `ensure_ascii=False`→`ensure_ascii=None` 変異)。CPython の `json` エンコーダは `ensure_ascii` を `if self.ensure_ascii:` という真偽判定でしか参照せず、`None` も `False` も偽値であるため両者は分岐が完全に一致し、あらゆる `obj` で出力バイト列が同一になる真の等価変異(実測でも `ensure_ascii=False` と `ensure_ascii=None` の出力が非ASCII文字を含め一致することを確認) |
| `hooks/lib/scanners.py` | 148 | 87.2 | 99.3 | `_gitleaks_argv` の `mode` 既定値("auto")をキー省略時の実挙動で固定、`"off"` 分岐が `_resolve_config_path` を呼ばずに早期returnすること(spyで検証)、`argv += [...]`(auto/dockerのconfig追加)を完全一致リストで固定、`_resolve_config_path` の `str(candidate)` を直接テストで固定、`_run_gitleaks` へ渡す `subprocess.run` の全kwargs(`input`/`timeout`/`capture_output`/`text`)をスタブで捕捉して厳密検証、JSON要素中の非dictをスキップしても後続要素の走査が続くこと(`continue` と `break` の違い)、`rule_id and secret` の AND 境界(片方欠落時は不採用)、`scan_secrets` が `cwd`/`argv`/`text` を下流関数へ正しく渡すことをspyで直接検証 | 0(下記1件は等価変異と確認したが、同じ行に既存の実変異〈`x__run_gitleaks__mutmut_14`: `not in`→`in`、`x__run_gitleaks__mutmut_16`: `(0,1)`→`(0,2)`〉が同居して killed 済みのため、pragma で行ごと除外しない) | 1件: `x__run_gitleaks__mutmut_15`(`if r.returncode not in (0, 1):` → `(1, 1)`)。returncode が 0 のときは早期return `[]` の到達経路が変わるだけで最終的に到達する行(`if r.returncode == 0: return []`)も `[]` を返すため観測結果は同一、returncode が 1 のときはどちらの式でも早期returnせず後続のJSONパースへ進み、それ以外の値では両式とも早期return `[]` となるため、あらゆる `returncode` で戻り値が変わらない真の等価変異(机上で全整数値を場合分けして確認) |
| `hooks/lib/config.py` | 161 | 77.0 | 92.5 | `load_config` 冒頭の `copy.deepcopy(DEFAULTS)` と型不一致リセット時の `copy.deepcopy(default_value)` を、返した cfg のネスト辞書を書き換えて `DEFAULTS` 側が無事なことを確認するテストで固定(id比較+実際の変異による汚染検証)、`cwd=None` 既定値("."→実カレントディレクトリ、`monkeypatch.chdir`で検証)、`Path.read_text` を spy 化して `encoding="utf-8"` が渡っていることを直接検証(大文字化・`None`化の両変異を1テストで killed)、broken-json/非dict-JSON の各層で `errors.append` のメッセージ全文一致と、その層のエラー後も `continue` で次の層(project層)を読み続けること(`break` との違い)を新規テストで固定、enum/`protected_branches`/`write_protected_paths`/`gitleaks_config` の各フォールバック警告メッセージを全文一致に強化、`gitleaks_image` 不正型のフォールバック(値・メッセージとも未テストだった)を新規追加 | 0(下記12件は等価変異と確認したが、同じ5行に `cfg.get("XXexfil_guardXX", {})`/`"EXFIL_GUARD"`/`.get("XXcategoriesXX", {})`/`"XXbash_guardXX"`/`"XXprotected_branchesXX"`/`"XXsecrets_guardXX"`/`"XXwrite_protected_pathsXX"`/`"XXscannersXX"` のようなキー文字列リテラル変異や `.get(None)`/`cfg.get({})` 変異など34件の実変異〈既存テストで killed 済み〉が同居しており、pragma を付けると mutmut がその行ごと変異対象から外して既存の検出を失うため、patterns/hook_io/scanners と同じ扱いで pragma を付けず表に記録するに留めた) | 12件: `x_load_config__mutmut_35`/`_37`(`value = cfg.get(section, {}).get(sub_key)` の第2引数を `None`化・省略)、`_43`/`_45`/`_47`/`_49`(`categories = cfg.get("exfil_guard", {}).get("categories", {})` の内側・外側 `.get` の第2引数を `None`化・省略)、`_67`/`_69`(`pb = cfg.get("bash_guard", {}).get("protected_branches")` の `.get` 第2引数)、`_95`/`_97`(`wp = cfg.get("secrets_guard", {}).get("write_protected_paths")` の `.get` 第2引数)、`_117`/`_119`(`sc = cfg.get("scanners", {})` の `.get` 第2引数)。対象キー(`exfil_guard`/`bash_guard`/`secrets_guard`/`scanners`、および enum ループの各section、`categories`)はいずれも直前の型検証ループ(`for key, default_value in DEFAULTS.items(): ... cfg[key] = copy.deepcopy(default_value)`)またはmerge/deepcopyの非削除性により、この時点で cfg に必ず dict のキーとして存在することが構造的に保証されるため、`.get(key, {})` の第2引数はどんな値に変異しても到達不能で、あらゆる入力に対して等価(ただし前述の通り同一行に実変異が同居するため pragma 未使用)。要コード変更(コードは変更していない、記録のみ): `exfil_guard.categories` に非dict値(例: 文字列)を与えると `for cat_key, cat_value in list(categories.items())` が `AttributeError` を送出し `load_config` が例外送出する(f08504b/8cd921dが確立した「絶対にraiseしない」不変条件への違反)。`cfg["exfil_guard"]["categories"]` は `_ENUM_KEYS`/`DEFAULTS` の型検証ループが検査するのはトップレベルの `exfil_guard` 自体の型のみで、そのネストした `categories` サブキーの型は検証していないため素通りする。修正には `categories` 自体の型検証(dictでなければ既定値へフォールバック)の追加が要るが、本タスクではテスト追加のみが許可されコード変更は対象外のため、別途ユーザー確認の上での修正が必要 |
| `hooks/pre_tool_use/bash_guard.py` | 303 | 66.3 | 100.0 | `_normalize` のクォート除去をシングル/ダブル両方で関数直接テスト化(片方しか除去しない変異=クォート回避の穴)し、`rm -rf '/'` のシングルクォート回避が deny になることを end-to-end でも固定、`_expand_simple_assignments` の `$VAR` 境界 `(?![A-Za-z0-9_])` を展開後文字列の完全一致で両方向固定(`$T` の直後が大文字 `$TMPDIR`・小文字 `$Tmp`・数字 `$T9`・`_` の場合は展開しない/区切り `$T-x` なら展開する)、`_has_sensitive_operand` のトークン正規化を True/False 両方向で固定(末尾スラッシュ付きパス・引用符付きトークンは機微判定される/剥がすのは引用符と末尾スラッシュだけで `X.env`・`…X` のような境界トークンは機微でない)、`_exfil_ask`/`evaluate` の判定辞書をキー名・理由文全文まで完全一致で固定(deny/ask/外部送信の3種、各 deny・ask 規則名が理由に載ることを規則ごとに検証)、`_force_push_rules` の3規則名と順序をリスト完全一致で固定+`protected_branches: []` で空リスト、キーが1つも無い設定 `{}` を渡して `enabled` 既定 True・`extra_deny`/`extra_ask`/`allow` の `[]` 既定を貫通(既定値を `None`/`False` にする変異を一括 kill)、テストが皆無だった `extra_ask` 経路を規則名込みで新規追加、`targets +=` → `targets =` 変異(原文と展開後の両方を検査する二重化)を `HOME=/tmp; rm -rf $HOME`(展開後は deny 相当でなくなるケース)で固定、`no tests` だった `main()` に同一プロセス駆動テスト12本を新規追加(deny/ask/安全コマンドは無出力/非Bashツール/不正イベント/`command` キー欠落は空文字列扱い/イベントの `cwd` からプロジェクト設定を読むこと/`bash_guard` セクション欠落時の `{}` 既定/`evaluate` 例外時の fail-close ask/設定エラーの `systemMessage` 合成)。いずれも出力JSON全体の完全一致と `SystemExit` コードの厳密比較 | 0(1行も変更していない。等価を疑った `cfg_all.get("bash_guard", {})` の第2引数変異〈`x_main__mutmut_18`/`_20`〉は「`load_config` が必ず `bash_guard` を含む dict を返す」不変条件により到達不能に見えたが、`config.load_config` 自体を monkeypatch でセクション欠落の戻り値に差し替えるテストで実際に kill できたため等価ではなかった) | 0(生存・no tests とも 0 件。ガード挙動の変更が必要な穴〈要ガード変更〉も検出されなかった) |

**教訓**(このファイルで見つかった、一般化できるもの):
- **「呼び出し先の戻り値契約」に依存した到達不能性は等価変異ではない — その呼び出し先を monkeypatch で差し替えれば kill できる**: `main()` の `cfg_all.get("bash_guard", {})` の `{}` 既定は、`load_config` が常に `bash_guard` キーを含む dict を返すという不変条件のもとでは到達不能で、config.py の「型検証ループが確立した不変条件」と同型の等価変異に見える。しかし不変条件を作っているのが**同一関数内の先行コードではなく別関数**である場合、その別関数をテストから差し替えれば防御的既定値の分岐に到達でき、変異は死ぬ。防御的既定値は「呼び出し先の契約が破れたときに何が起きるか」を規定する実挙動なので、これをテストで固定するのは正当(契約違反時に deny 層が沈黙しないことの保証になる)。等価判定の前に「その不変条件はテストから壊せるか」を必ず問うこと。
- **`no tests` の `main()` は、出力の完全一致テスト1本で大量の変異が一斉に死ぬ**: 出力JSON全体の完全一致(キー名・decision・reason 全文)と `SystemExit` コードの厳密比較を1本書くだけで、引数の入れ替え・省略・`None` 差し替え・辞書キー文字列の変異(`"XXreasonXX"` 等)がまとめて kill される。加えて「静かに通す」経路を `out == ""` で固定すると、例外→fail-close ask へ倒れる変異(`cfg = None`・`command = None` 等)を捕まえられる。deny/ask/無出力/早期return(非対象ツール)の4経路を押さえるのが最小構成。
- **黒箱の subprocess テストは mutation score に寄与しない**: `bash_guard.py` を別プロセスで実行する既存の end-to-end テストがあっても、mutmut は `mutants/` 配下のモジュールを import して変異させるため子プロセスには変異が届かず、`main()` は `no tests` のまま残る(実際 62 件が `no tests` だった)。エントリポイントの変異を潰すには、`load_hook` + `monkeypatch.setattr("sys.stdin", …)` + `capsys` で**同一プロセス内から** `main()` を呼ぶテストが必須。
- **文字集合を引数に取る `strip`/`rstrip`/`lstrip` の変異は、集合の内外を1文字だけ区別する入力で死ぬ**: `rstrip("/")` → `rstrip(None)`(空白剥がし)・`lstrip("/")`・`strip("\"'")` → `strip("XX\"'XX")` のような変異は、「末尾スラッシュ付きのパス」「`X` で始まる/終わるファイル名」のような境界トークンを1つ入れるだけで挙動差が出る。文字列メソッドの引数が**集合**である場合は、集合に属する文字/属さない文字の両方を端に持つ入力を用意する(片方向だけだと `X` を含むよう拡張された集合の変異が生き残る)。
- **mutmut のインクリメンタル実行は「テストだけを変更」しても自動で拾われないことがある**: 変異対象ファイル(`hooks/lib/*.py`)を触らずテストだけ追加すると、`function_hashes` は不変のため `uv run mutmut run` が既存の生存/死亡ステータスをキャッシュのまま再利用し、`mutants/*.py.meta` が更新されない(進捗バーは 803/803 まで進むが 0.00 mutations/second で終わる)。対象の生存 ID を明示して `uv run mutmut run <id> <id> …` を渡すと強制的に再実行され、正しく反映される。テストのみ追加したタスクでは、この明示再実行を挟んでから `verify.py mutation` を確認すること。→ この知見を受けて `scripts/verify.py mutation` は実行前に `mutants/` を削除して常にフル実行する(fix(verify): mutation ステージは mutants/ を消してから mutmut を実行)。手動で `mutmut run` を回すときだけ明示 ID 指定が要る。
- **等価変異は「行」ではなく「変異」の単位で判定し、pragma は行が実変異と同居していないことを確認してから使う**: `# pragma: no mutate` は mutmut では行単位で効き、同じ行に生きた(実際に検出可能な)変異が同居していると、pragma によってその行ごと変異対象から外れ、既に通っている検出も失われる。等価変異と実変異が同じ行にまたがる場合は pragma を付けずに、この表のように「残り」として記録するに留めるのが安全(`.claude/rules/guard-rule-changes.md` の「価値ある変異と同居する行は先に文を分割する」原則の変種で、行分割そのものが不可な今回はドキュメント記録で代替した)。
- **`if d > 9` のような偶数専用の分岐は `>` と `>=` が構造的に等価になりうる**: 直前で `d *= 2` している変数に対する閾値比較は、閾値の奇偶と変数の取り得る値の奇偶を見れば境界差が実在するか机上で判定できる(今回は 9 が奇数・d が常に偶数のため無意味な境界だった)。
- **ライブラリ内部が真偽判定でしかキーワード引数を見ていない場合、値そのものを変える変異(`False`→`None` 等)が真の等価変異になりうる**: `json.dump(..., ensure_ascii=False)` を `ensure_ascii=None` に変える変異は、CPython の `json` エンコーダが `if self.ensure_ascii:` としか判定しないため、`False` と `None`(いずれも偽値)で完全に同じ分岐・同じ出力になる。stdlib/依存ライブラリの実装が真偽判定かどうかを一次情報(ソース)で確認してから等価と判断すること。
- **薄いオーケストレーション関数(引数をそのまま下流関数へ渡すだけ)の生存変異は、下流関数を monkeypatch でスパイに差し替えて渡された引数を厳密比較するテストで機械的に潰せる**: `scan_secrets` が `cwd`/`argv`/`text` を正しく下流(`_gitleaks_argv`/`_run_gitleaks`)へ渡しているかは、戻り値だけを見るテストでは `None` への差し替え変異を見逃す。下流関数を fake に差し替えて受け取った引数を capture し、期待値と完全一致で比較すれば1テストで複数の「引数を別の値/`None`に差し替える」変異をまとめて killできる。同様に `subprocess.run` のような外部呼び出しも、呼び出しをスタブに差し替えて渡された全 kwargs(`input`/`timeout` 等)を厳密比較すると、kwarg 値の変異と kwarg 自体の欠落変異を同時に潰せる。
- **早期の型検証ループが「このキーはこの後dictとして必ず存在する」という不変条件を確立している設計では、以降の `dict.get(key, {})` の第2引数(default)は構造的に到達不能な真の等価変異になりうる — ただしそれは pragma できることを意味しない**: `load_config` は `DEFAULTS.items()` を全走査してトップレベル各キーの型を検証・不一致なら既定値で上書きするループを、後続の enum/list 検証より先に実行しており、`_merge` もキーを削除しない(base の全キーを保持し override は上書きのみ)。この2条件が揃うと、後続コードの `cfg.get(<検証済みトップレベルキー>, {})` は入力に関わらず必ずキーがヒットし、`{}` フォールバックへは絶対に落ちない、という等価性の判定自体は正しい。しかし `cfg.get("exfil_guard", {})` のような呼び出しは、第2引数だけでなく `"exfil_guard"` というキー文字列リテラルも変異対象になり、そちらは `"XXexfil_guardXX"`・`"EXFIL_GUARD"` 等の実変異として既存テストで killed 済みであることが通常で、同じ行に同居する。pragma は行単位で効くため、第2引数だけが等価だからと安易に pragma すると、同居しているキー文字列の実変異まで denominator ごと消してスコアを見かけ上つり上げてしまう(config.py で実際に発生: 12個の等価変異を pragma した結果、無関係な34個の実変異も一緒に消え、77.0→100.0という誤った数値が出た)。等価性を確認した後は必ず `mutmut show <id>` で**その行の全 mutant ID**を洗い出し、実変異が1つでも同居していれば pragma せず「残り」に記録するに留める(この判断は既存の教訓「等価変異は行でなく変異の単位で判定する」の具体例)。
- **pragma の理由文が長すぎて行長制限(ruff E501)を超える場合、理由文は pragma と同じ行に収めず、直前の独立したコメント行に切り出せる**: mutmut は「`# pragma: no mutate` の文字列が、変異対象トークンを含む行に存在するか」だけを見るため、理由の説明を1行前の通常コメントへ移し、pragma 対象の行には短い `# pragma: no mutate`(理由なし)だけを残しても検出は効く。ruff の行長カウントは全角(CJK)文字を幅2として数えるため、和文の理由コメントは半角換算で見た目の2倍の予算を消費する点に注意(単純な `len()` では読めない)。

## 第2段階本体に持ち越すもの

- Task 0 = import 再構成(上記 4.)。`hooks/` は稼働中のガードなので、1 ファイルずつ原子的に書き換え、直後に pytest と全 hook の直接実行スモークで確認する
- 生き残り計 130 + no tests 62 のトリアージ(ファイル別タスク)。正体の記録はこの文書に追記する
