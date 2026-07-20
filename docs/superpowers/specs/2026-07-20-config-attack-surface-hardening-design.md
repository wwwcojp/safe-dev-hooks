# 設定・フック改変面のハードニング(.mcp.json保護・ダウンロード書込・ConfigChange検知)設計

- 日付: 2026-07-20
- 対象バージョン: 0.5.0
- ステータス: 承認済み(ブレインストーミングで各セクション承認)

## 背景と目的

`docs/best-practices.md` の再調査(2026-07)で取り込んだ知見 — 特に §6.2「Hooks自体が攻撃面になる」(CVE-2025-59536 / CVE-2026-21852: Claude Code のプロジェクト設定ファイル群を攻撃面とした RCE・APIキー窃取)— を現行実装と突き合わせた結果、write_protected(0.4.0 で導入した自己保護層)に以下の未対応経路が見つかった。

1. **`.mcp.json`(MCPサーバ定義)が書込保護の対象外** — MCPサーバの `command` は任意コマンド実行経路であり、フック定義(`settings.json`)と同格の改変標的。グローバル設定 `.claude.json` も同様。
2. **ダウンロードによる設定上書きが素通り** — write_protected の Bash 検査はシェル変異キーワード(リダイレクト・`tee` 等)のみを見るため、`curl -o .claude/settings.json <URL>` のような出力フラグ経由の書込を検査しない。シェルコマンド文字列から機械判定できる範囲であり、「機械判定できるものは確実に止める」方針(security-model §3)上は塞ぐべき側。
3. **write_protected が見えない経路の設定変更が完全に不可視** — インタプリタレベル書込(既知の限界#9)・外部プロセス・人間の手による編集で設定が変わっても、セッション中に気づく手段がない。公式 Hooks に追加された `ConfigChange` イベント(設定ファイル変更時に発火)で検知できる。

1・2 は deny 層(予防)の拡張、3 は新しい検知層の追加である。いずれも deny を弱める変更ではない。

## 決定事項

| 論点 | 決定 |
|---|---|
| 進め方 | プロトタイプ実装(未コミット)は patch へ退避し**白紙から再実装**。patch は参照専用で適用しない |
| config_guard の動作 | **警告専用**(ブロックしない)。理由は挙動仕様 #3 |
| 作業単位 | 1ブランチ(`feat/config-attack-surface-hardening`)・スペック1本・リリース 0.5.0(新フック追加 = minor) |
| 実装アプローチ | 案A: ターゲット検査+独立小フック。curl/wget をキーワード変異子(全トークン照合)に足す案Bは読取URLの誤denyを生むため不採用。audit_log 配線のみの案Cは可視化価値を捨てるため不採用 |
| スコープ外 | `.claude/skills/`・`.claude/agents/` の保護(頻繁な正当編集がありask層新設に見合わないと判断)。`curl -O`・裸の `wget URL`(ファイル名がURL側から決まる形式)。インタプリタレベル書込(config_guard が事後検知で補完) |

## 挙動仕様

### #1 `.mcp.json`・`.claude.json` の書込保護

`rules/sensitive_paths.json` の `write_protected` に `.mcp.json`・`.claude.json` の2エントリを追加する。コード変更なし(データ駆動)。

- basename 一致により所在を問わず保護する。guard-rule-changes 原則1(裸 basename 禁止)には抵触しない — 両名とも Claude Code 固有の規約名であり、`settings.json` のような汎用名と異なり無関係ファイルへの誤爆が想定されないため。
- 読取(`Read`・`cat` 等)は従来どおり許可。`Edit`/`Write` と Bash 変異コマンド(#2 で追加する出力フラグ検査を含む)のみ deny。

### #2 curl/wget 出力フラグの書込検査

`secrets_guard` に `_download_output_tokens(seg: str) -> list[str]` を新設する。セグメント内に `\b(curl|wget)\b` があるとき、**出力フラグの引数トークンのみ**を収集し、既存の write_protected 照合ループへ合流させる(既存の `_mutation_target_tokens` とは独立の収集パス。セグメントが変異キーワードを含む場合は両方の候補を照合する)。

対応形式:

| ツール | 形式 | 例 |
|---|---|---|
| curl | `-o FILE`(バンドル末尾 `-fsSLo FILE`・密着 `-oFILE` を含む) | `curl -fsSLo .mcp.json URL` |
| curl | `--output FILE` / `--output=FILE` | `curl --output .claude/settings.json URL` |
| wget | `-O FILE`(密着 `-OFILE` を含む) | `wget -O .claude/settings.json URL` |
| wget | `--output-document FILE` / `--output-document=FILE` | `wget --output-document=.mcp.json URL` |
| wget | `-o FILE` / `--output-file=FILE`(ログ書込・上書き) | `wget -o .claude-hooks.json URL` |
| wget | `-a FILE` / `--append-output=FILE`(ログ追記) | `wget -a .mcp.json URL` |

- 短縮フラグは `^-[A-Za-z]*o(\S*)$`(curl)/ `^-[A-Za-z]*[Ooa](\S*)$`(wget)で照合する。固定幅アンカー・入れ子量化子なし(原則3、ReDoS安全)。キャプチャが空ならば次トークンを引数とみなす。
- curl の `-O` はリモート名保存(引数なし)のため対象外。
- wget の `-o`/`--output-file`(ログ書込)・`-a`/`--append-output`(ログ追記)も保護ファイルの改変経路として検査対象に含める(いずれも指定FILEへ書込または追記する)。
- セグメント内に curl と wget が混在する場合(例: `curl URL | wget -O FILE -`)は、`_SEGMENT_RE` がパイプでは分割しないため両者が同一セグメントに現れる。この場合は一方のみでなく両方のツールのフラグ集合を適用して照合する。
- 誤検知回避(原則2): 照合対象は出力フラグの引数のみ。URL・データ文字列・その他トークンは照合しない。したがって保護ファイル名を含むURLの読取(`curl https://…/.claude/settings.json`)、`/tmp` 等への保存、`wget -O -`(標準出力)は deny されない。

### #3 config_guard(ConfigChange 検知層)

新規フック `hooks/config_change/config_guard.py`(1 Hook = 1関心事の構成に従い専用ディレクトリ)。`ConfigChange` イベントへ matcher なし(全変更元: user/project/local/policy/skills)・timeout 10秒で配線し、同イベントへ `audit_log` も配線(`async: true`)する。

動作(警告専用):

1. 変更元を含む `systemMessage` で「セッション中に設定ファイルが変更された」ことを通知する。変更元フィールド名は公式ドキュメントに明記がないため、イベント入力の `source` → `config_source` → `matcher` の順で防御的に読み、いずれも無ければ「不明」とする。
2. 変更後にユーザー/プロジェクト設定(`$HOME/.claude/settings.json`・`.claude/settings.json`・`.claude/settings.local.json`)のいずれかで `disableAllHooks: true` が有効なら、「全Hooks(本ガードを含む)が無効化される」旨の警告を追加する。壊れたJSONは黙って読み飛ばす。

ブロックしない理由(設計判断):

- `disableAllHooks` は本Hooks集が公式に認める唯一の完全無効化手段(security-model §2)であり、ブロックすると正規の解除経路と矛盾する。
- `ConfigChange` は人間自身の設定編集でも発火し、フックからは変更者を区別できない。ブロックは正当な設定作業を止める(原則2)。
- 新しい検出はまず警告として観測する(warn→block 段階導入、best-practices §6.3)。

位置づけ: write_protected(予防層)の背後の**検知層**。security-model §3 の「`disableAllHooks` による無効化は防げない」を「防げないが、黙っては起きない」へ引き上げる。

設定・フェイルモード:

- 設定キー `config_guard.enabled`(既定 `true`)。警告専用 = deny層ではないため、`enabled: false` で完全に無効化できる(deny層の `enabled:false` 免疫原則とは矛盾しない。ドキュメントに明記)。
- fail-open(`hook_io.fail_open`)。検知層の異常でツール実行・設定変更を止めない。
- ユーザーホーム参照はモジュール定数 `_USER_SETTINGS` とし、テストから monkeypatch で隔離できるようにする。

互換性: `ConfigChange` は新しめのイベントであり、未対応の Claude Code では単に発火しない(他フックへ影響なし)。docs に注記する。

## テスト(原則4: 両方向)

- `tests/test_secrets_guard.py`:
  - deny 側: `.mcp.json`/`.claude.json` への Edit/Write、`curl -o .claude-hooks.json URL`、`-fsSLo` バンドル、密着 `-o.claude-hooks.json`、`--output .claude/settings.json`、`wget -O .claude/settings.json`、`--output-document=.mcp.json`、`&&` 連結コマンド内での出現。
  - 素通り側: 保護ファイル名を含むURLの読取、`curl -o /tmp/page.html`、`--output result.json`、`curl -O URL`、`wget -O - URL`、裸の `wget URL`、`.mcp.json` の Read/`cat`。
- 新規 `tests/test_config_guard.py`: 通知内容(変更元を含む)、変更元不明時のフォールバック、`disableAllHooks` 警告の有無両方、`enabled:false` での完全無効化、壊れた settings.json の読み飛ばし。
- `tests/test_packaging.py`: 配線イベント集合へ `ConfigChange` を追加。`sensitive_paths.json` の検証キーへ `write_protected` を追加。

## ドキュメント・リリース

- 新規: `docs/hooks/config_guard.md`(目的・動作・ブロックしない設計判断・互換性注記)。
- 更新: `docs/hooks/secrets_guard.md`(write_protected 対象・curl/wget 検査・既知の限界2件)、`docs/security-model.md`(§3 補完・限界#9 更新・fail-open 一覧)、`docs/configuration.md`(`config_guard.enabled`)、README 日英(9本化・テーブル行・audit_log 行)、`docs/hooks/audit_log.md`、`examples/settings.full.json`、`CHANGELOG.md`(`[0.5.0]`)。
- バージョン: `pyproject.toml`・`.claude-plugin/plugin.json` を 0.5.0 へ。
- 実装時の制約: `hooks/`・`rules/` は自ガードの write_protected 対象のため、Bash 経由の Python スクリプトで編集する(`.claude/rules/dogfooding.md`)。
