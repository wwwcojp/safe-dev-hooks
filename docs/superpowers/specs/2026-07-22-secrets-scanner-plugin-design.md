# 秘密検出スキャナのプラグイン化(gitleaks 委譲)設計

- 日付: 2026-07-22
- スコープ: 秘密(credentials)検出のみ。PII/Presidio は別 spec に分離する。
- 関連規約: `.claude/rules/guard-rule-changes.md`(特に原則4・原則5)、`.claude/rules/dogfooding.md`

## 背景・目的

`exfil_guard`・`secrets_scan`・`exfil_output_scan` の3フックは、秘密検出を自作の
`hooks/lib/patterns.py`(`secret_patterns.json` の手書き正規表現)に依存している。
手書きルールは網羅性・誤検知調整の面で成熟 OSS に劣る。本設計は、成熟した秘密検出 OSS
である **gitleaks** を「あれば使う」任意バックエンドとして加算し、内蔵検出は floor として
残したままカバレッジを広げる。

実地確認(gitleaks 8.30.1)では、内蔵 patterns が拾う AWS 例示アクセスキー
(`AKIA…EXAMPLE` 形式のダミー)を gitleaks は既定 allowlist で除外し、逆に gitleaks は
`github-pat` を拾う。両者は相互補完的であり、**union(加算)**が最も検出漏れを減らす。

## 設計原則(不変条件)

1. **union であって置換ではない。** `secret_patterns.json` による内蔵検出は常に実行される
   floor であり、gitleaks の結果はその上に加算されるのみ。gitleaks が不在・異常終了しても
   内蔵検出は一切変化しない。
2. **deny 保証を弱めない。** `exfil_guard` の `credentials` カテゴリは既定 `deny`(設定で
   解除不可)。gitleaks 由来の findings も `credentials` に入れる(deny 対象を広げる方向)。
   deny の解除経路は増やさない。よって `.claude/rules/guard-rule-changes.md` 原則5に
   抵触しない(deny を弱める変更ではなく、加算で強める変更)。
3. **fail-open。** バックエンドの不在・タイムアウト・異常終了・パース失敗はすべて floor のみを
   返す。ツール実行は止めない。既存フックの `hook_io.fail_open` / `semantic_check` の
   前例に準拠する。
4. **可搬性を壊さない。** gitleaks は PEP723 のインライン依存に**加えない**。既存の
   `quality_gate`(ruff 委譲)・`exfil_guard.semantic_check`(claude 委譲)と同様、
   `shutil.which` でガードした外部プロセスとしてのみ呼ぶ。不在なら完全に無コスト。

## コンポーネント境界: `hooks/lib/scanners.py`(新規)

秘密検出の集約点を1モジュールに新設する。3フックはこの関数を呼ぶだけにする。

```
scanners.scan_secrets(text: str, cfg_all: dict, cwd: str | None) -> list[dict]
    戻り値: [{"rule": str, "match": str}, ...]  (patterns.scan_text と同一契約)

    手順:
      1) builtin = patterns.scan_text(text, patterns.load_rules("secret_patterns.json"))
      2) sc = cfg_all.get("scanners") or {}
         argv = _gitleaks_argv(sc, cwd)   # mode/実行形態/設定ファイルを解決。使えなければ None
         if argv is not None:
             builtin += _run_gitleaks(argv, text)   # 例外時は [] を足す(fail-open)
      3) (rule, match) タプルで重複排除して返す(初出順維持)
```

- `scan_secrets` は `cwd`(`event.get("cwd")`)も受け取り、設定ファイル自動検出に用いる。
- PII 側(`pii_patterns.json`)は本 spec のスコープ外。今回は一切変更しない。
- `scan_secrets` 自身は例外を上位に投げない(内部で握って floor を返す)。ただし
  内蔵 `patterns.scan_text` の例外は既存どおり各フックの `fail_open` に委ねるため、
  gitleaks 部分のみを try で囲む。

### gitleaks コマンド組み立て `_gitleaks_argv(sc, cwd) -> list[str] | None`

実行形態(ネイティブ / Docker)と設定ファイルをここで一元的に解決する。使えない場合は
`None` を返し、呼び出し側は gitleaks 加算をスキップする(fail-open)。

```
共通フラグ: stdin --report-format json --report-path - --no-banner -l error
mode = sc.get("gitleaks", "auto")

設定ファイル解決 cfg_path:
  explicit = sc.get("gitleaks_config")            # 明示指定が最優先
  cfg_path = explicit if explicit else
             (<cwd>/.gitleaks.toml if 存在 else None)   # 無ければ gitleaks 既定ルールセット

mode == "off"                      -> None
mode == "auto":
  which("gitleaks") が None       -> None
  それ以外 -> ["gitleaks", *共通フラグ, *(["-c", cfg_path] if cfg_path else [])]
mode == "docker":
  which("docker") が None         -> None
  image = sc.get("gitleaks_image", "ghcr.io/gitleaks/gitleaks:v8.30.1")
  base  = ["docker", "run", "--rm", "-i"]
  cfg_path があれば:
     コンテナ内 /tmp/gl.toml へ read-only マウントし -c で指す
     base += ["-v", f"{abspath(cfg_path)}:/tmp/gl.toml:ro"]
     tail  = ["-c", "/tmp/gl.toml"]
  else: tail = []
  -> [*base, image, *共通フラグ, *tail]
```

- **"auto" は docker へフォールバックしない。** `auto` は `which("gitleaks")` の軽量チェック
  のみ。`docker run` はコールドスタートが重く(数百ms〜秒)、短命な per-call プロセスでは
  コストが大きいため、Docker は明示 `mode:"docker"` に限定する。

### gitleaks 実行 `_run_gitleaks(argv, text) -> list[dict]`

```
入力:  text を stdin へ(UTF-8)、subprocess.run(input=text, timeout=GITLEAKS_TIMEOUT_SEC)
GITLEAKS_TIMEOUT_SEC = 15
終了コード:
  0 → 検出なし([] を返す)
  1 → 検出あり(既定 --exit-code 1)。stdout の JSON をパース
  それ以外 → 異常。[] を返す(fail-open)
パース:
  JSON 配列。各要素 f について
    {"rule": f"gitleaks:{f['RuleID']}", "match": f["Secret"]}
  JSON パース失敗・想定外形状 → [] を返す(fail-open)
```

- `match` に `Secret` を用いるため、`exfil_output_scan` の `action:"redact"`
  (`match` を `.replace` でマスキング)ともそのまま両立する。
- 再帰の懸念なし(gitleaks は claude を呼ばない)。`semantic_check` のような env ガードは不要。

### Docker モードの制約(spec 上の明示事項)

- **タイムアウト整合**: `docker run` のコールドスタートは、hook timeout が短い
  `secrets_scan`(10s)・`exfil_output_scan`(15s)では予算を食い潰しうる。Docker モードは
  実質 `exfil_guard`(60s)向き。短タイムアウトのフックで Docker を使う場合、
  タイムアウトで加算されず内蔵のみ(fail-open)に落ちうる点を運用上許容する。
- **プライバシー(重大)**: payload には秘密が含まれる。`DOCKER_HOST` がリモートを指す環境
  では、秘密検査対象のデータがリモート docker デーモンへ送信され得る。**Docker モードは
  ローカルデーモン前提**とし、README/ドキュメントで明示的に注意喚起する。
- **サプライチェーン**: イメージは既定でバージョン固定
  (`ghcr.io/gitleaks/gitleaks:v8.30.1`)。`scanners.gitleaks_image` で上書き可能だが、
  `:latest` の使用は固定タグ/ダイジェスト推奨と併記する。

### 設定ファイル `.gitleaks.toml` の扱いと安全性

- gitleaks の `stdin` はターゲットパスを持たないため `(target)/.gitleaks.toml` の自動適用が
  効かない。honor するには明示的に `-c <path>` を渡す必要がある(上記解決ロジック)。
- **安全性の要点**: 設定ファイルの `[allowlist]` は gitleaks の**加算分**を抑制できるだけで、
  内蔵 floor(`secret_patterns.json` の `credentials=deny`)には一切触れない。よって
  広すぎる/悪意ある `.gitleaks.toml` allowlist でも **deny 保証は不変**であり、
  `.claude/rules/guard-rule-changes.md` 原則5(deny を設定で解除不可)に整合する。

## 3フックの変更(最小)

いずれも「内蔵 secret スキャン呼び出し」を `scanners.scan_secrets(text, cfg_all, cwd)` に
差し替えるだけ。PII・custom_patterns・confidential_markers 等の他カテゴリは不変。`cwd` は
各フックが既に保持している `event.get("cwd")` を渡す(設定ファイル自動検出に使う)。

- `hooks/pre_tool_use/exfil_guard.py` `evaluate()`:
  `add("credentials", patterns.scan_text(payload, load_rules("secret_patterns.json")))`
  → `add("credentials", scanners.scan_secrets(payload, cfg_all, cwd))`
  ※ `evaluate` は現在 `cfg`(exfil_guard セクション)のみ受け取るため、`cfg_all` と `cwd` を
    渡せるようシグネチャを調整する(呼び出し元 `main` は両方を保持済み)。
- `hooks/post_tool_use/secrets_scan.py` `main()`:
  内蔵 `load_rules("secret_patterns.json")` によるスキャン部分を
  `scanners.scan_secrets(text, cfg_all, cwd)` に差し替え。`custom_patterns` は従来どおり
  別途 `patterns.scan_text` で加算する(union にマージ)。
- `hooks/post_tool_use/exfil_output_scan.py` `evaluate()`:
  secret 部分を `scanners.scan_secrets(text, cfg_all, cwd)` に差し替え、pii は据え置き。
  `evaluate` に `cfg_all` と `cwd` を渡せるようシグネチャ調整。

## 設定スキーマ(`hooks/lib/config.py`)

3フック共通のため、フック配下ではなくトップレベルに `scanners` セクションを新設する。
既存のフラットな enum 検証様式に合わせ、オブジェクト化せず sibling キーで拡張する。

```
DEFAULTS["scanners"] = {
    "gitleaks": "auto",                                   # "auto" | "off" | "docker"
    "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1", # mode=docker のイメージ(固定タグ)
    "gitleaks_config": None,                              # -c に渡すパス。None=自動/既定
}
_ENUM_KEYS[("scanners", "gitleaks")] = {"auto", "off", "docker"}
```

- `gitleaks`(mode): 既定 `auto`。`auto`=PATH に gitleaks があれば加算/無ければ無コスト。
  `off`=内蔵のみ。`docker`=`docker run` 経由(明示 opt-in)。不正値は `auto` へフォールバック。
- `gitleaks_image`: 文字列検証。`docker` モードでのみ使用。既定はバージョン固定タグ。
- `gitleaks_config`: 文字列 or `None` を検証(不正型は `None` にフォールバック)。指定時は
  `-c` に渡す。未指定時は `<cwd>/.gitleaks.toml` を自動採用、無ければ gitleaks 既定。
- いずれの不正値・型不正も既存の検証パターンに従い既定へフォールバックし `_errors` に記録。

## フェイルモード整理

| 状況 | 挙動 |
|---|---|
| gitleaks 不在(`which` 失敗) | 内蔵のみ。無コスト |
| `scanners.gitleaks: "off"` | 内蔵のみ |
| `mode:"docker"` だが docker 不在 | 内蔵のみ(`_gitleaks_argv` が None) |
| gitleaks/docker タイムアウト/異常終了/パース失敗 | 内蔵のみ(gitleaks 分は加算されない) |
| gitleaks 検出あり(exit 1) | 内蔵 ∪ gitleaks |
| 内蔵 `patterns.scan_text` が例外 | 各フックの `fail_open`(既存挙動、ツールは止めない) |

deny 保証: どの fail-open 経路でも内蔵 `secret_patterns.json` による `credentials` 検出は
不変。gitleaks は常に「上乗せ」でしかない。

## テスト(原則4: false-negative と false-positive の両方向)

CI では実 gitleaks バイナリに依存しない。**スタブ実行ファイル**を一時ディレクトリに置き、
`shutil.which` が解決するよう `PATH` を差し替える方式でバックエンドを注入する
(実バイナリ非依存・決定論的)。

- **union で拾う(false-negative 防止)**: 内蔵が拾わず gitleaks(スタブ)が拾う秘密を
  スタブが JSON+exit1 で返すよう仕込み、`scan_secrets` の戻り値に
  `gitleaks:<rule>` が含まれること。
- **floor 不変(退行防止)**: gitleaks スタブが (a) 不在 (b) exit≠0/1 (c) 不正JSON の
  各ケースで、内蔵検出結果が単独実行時と一致すること。
- **正当テキストで誤検出しない(false-positive)**: 秘密を含まないテキストで
  `scan_secrets` が空を返すこと(内蔵・gitleaks とも)。
- **deny 保証**: `exfil_guard` で gitleaks 由来 finding が `credentials`(deny)に
  入り、decision が `deny` になること。
- **設定契約**: `scanners.gitleaks: "off"` で gitleaks が呼ばれず内蔵のみになること。
  不正値が `auto` にフォールバックし `_errors` に載ること。
- **redact 両立**: `exfil_output_scan` `action:"redact"` で gitleaks の `Secret` が
  `[REDACTED:gitleaks:<rule>]` に置換されること。
- **argv 組み立て(実行なしで検証)**: `_gitleaks_argv` を純関数として単体テストする。
  - `mode:"auto"` かつ gitleaks あり → `["gitleaks", …共通フラグ…]`。
  - `mode:"docker"` かつ docker あり → `["docker","run","--rm","-i", image, …]`、
    `gitleaks_image` 上書きが反映されること。
  - `mode:"docker"` かつ docker 不在 → `None`。
  - `gitleaks_config` 明示指定時 → `-c <path>` が付くこと(docker では `-v …:ro` と
    コンテナ内 `-c` が付くこと)。
  - `gitleaks_config` 未指定かつ `<cwd>/.gitleaks.toml` 存在時 → 自動で `-c` が付くこと。
    存在しなければ `-c` が付かないこと。
  ※ `which` は `shutil.which` をモンキーパッチ、ファイル存在はテンポラリ `cwd` で制御し、
    実 gitleaks / 実 docker には一切依存しない。

## ドキュメント更新

- `docs/hooks/secrets_scan.md` / `exfil_guard.md` / `exfil_output_scan.md`:
  任意 gitleaks 委譲・union・fail-open・既定 `auto` を追記。
- `docs/configuration.md`: トップレベル `scanners` セクション(`gitleaks` / `gitleaks_image`
  / `gitleaks_config`)と Docker モードの使い方を追加。
- `docs/security-model.md`: 「gitleaks は加算であり deny 保証を弱めない(`.gitleaks.toml`
  allowlist でも floor は不変)」および「Docker モードは `DOCKER_HOST` がリモートだと
  payload を外部送信し得る/ローカルデーモン前提」を明記。
- `CHANGELOG.md`: 次バージョンに Added として記載。

## スコープ外(別 spec / 将来拡張)

- PII 検出の Presidio 委譲(spaCy コールドロード=常駐サーバ前提のため独立設計が必要)。
- detect-secrets / trufflehog 等の追加バックエンド。
- Docker イメージのダイジェスト固定・自動 pull の可否判定(初回 pull コストの扱い)。
- 完全カスタムな argv 上書き(escape hatch)。今回は `mode`/`image`/`config` に限定(YAGNI)。
- バージョン番号の確定と CHANGELOG の版建ては実装プラン側で扱う。
