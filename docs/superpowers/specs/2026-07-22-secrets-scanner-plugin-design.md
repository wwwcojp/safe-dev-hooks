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
scanners.scan_secrets(text: str, cfg_all: dict) -> list[dict]
    戻り値: [{"rule": str, "match": str}, ...]  (patterns.scan_text と同一契約)

    手順:
      1) builtin = patterns.scan_text(text, patterns.load_rules("secret_patterns.json"))
      2) mode = (cfg_all.get("scanners") or {}).get("gitleaks", "auto")
         if mode == "auto" and shutil.which("gitleaks"):
             builtin += _scan_gitleaks(text)   # 例外時は [] を足す(fail-open)
      3) (rule, match) タプルで重複排除して返す(初出順維持)
```

- PII 側(`pii_patterns.json`)は本 spec のスコープ外。今回は一切変更しない。
- `scan_secrets` 自身は例外を上位に投げない(内部で握って floor を返す)。ただし
  内蔵 `patterns.scan_text` の例外は既存どおり各フックの `fail_open` に委ねるため、
  gitleaks 部分のみを try で囲む。

### gitleaks 呼び出し `_scan_gitleaks(text) -> list[dict]`

```
コマンド:
  gitleaks stdin --report-format json --report-path - --no-banner -l error
入力:  text を stdin へ(UTF-8)
timeout: GITLEAKS_TIMEOUT_SEC = 15
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
- gitleaks はリポジトリ内 `.gitleaks.toml` を自動適用しうるが、`stdin` はパスを持たない
  ため既定設定で動く。プロジェクト固有設定を尊重したい場合の `-c` 指定は本 spec の
  スコープ外(将来拡張)。
- 再帰の懸念なし(gitleaks は claude を呼ばない)。`semantic_check` のような env ガードは不要。

## 3フックの変更(最小)

いずれも「内蔵 secret スキャン呼び出し」を `scanners.scan_secrets(text, cfg_all)` に
差し替えるだけ。PII・custom_patterns・confidential_markers 等の他カテゴリは不変。

- `hooks/pre_tool_use/exfil_guard.py` `evaluate()`:
  `add("credentials", patterns.scan_text(payload, load_rules("secret_patterns.json")))`
  → `add("credentials", scanners.scan_secrets(payload, cfg_all))`
  ※ `evaluate` は現在 `cfg`(exfil_guard セクション)のみ受け取るため、`cfg_all` を
    渡せるようシグネチャを調整する(呼び出し元 `main` は `cfg_all` を保持済み)。
- `hooks/post_tool_use/secrets_scan.py` `main()`:
  内蔵 `load_rules("secret_patterns.json")` によるスキャン部分を
  `scanners.scan_secrets(text, cfg_all)` に差し替え。`custom_patterns` は従来どおり
  別途 `patterns.scan_text` で加算する(union にマージ)。
- `hooks/post_tool_use/exfil_output_scan.py` `evaluate()`:
  secret 部分を `scanners.scan_secrets(text, cfg_all)` に差し替え、pii は据え置き。
  `evaluate` に `cfg_all` を渡せるようシグネチャ調整。

## 設定スキーマ(`hooks/lib/config.py`)

3フック共通のため、フック配下ではなくトップレベルに `scanners` セクションを新設する。

```
DEFAULTS["scanners"] = {"gitleaks": "auto"}
_ENUM_KEYS[("scanners", "gitleaks")] = {"auto", "off"}
```

- 既定 `auto`(gitleaks が PATH にあれば加算、無ければ無コストでスキップ)。
- 不正値・型不正は既存の検証パターンに従い既定 `auto` にフォールバックし `_errors` に記録。
- `"off"` で内蔵 patterns のみに戻る(gitleaks を明示的に無効化)。

## フェイルモード整理

| 状況 | 挙動 |
|---|---|
| gitleaks 不在(`which` 失敗) | 内蔵のみ。無コスト |
| `scanners.gitleaks: "off"` | 内蔵のみ |
| gitleaks タイムアウト/異常終了/パース失敗 | 内蔵のみ(gitleaks 分は加算されない) |
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

## ドキュメント更新

- `docs/hooks/secrets_scan.md` / `exfil_guard.md` / `exfil_output_scan.md`:
  任意 gitleaks 委譲・union・fail-open・既定 `auto` を追記。
- `docs/configuration.md`: トップレベル `scanners` セクションを追加。
- `docs/security-model.md`: 「gitleaks は加算であり deny 保証を弱めない」を明記。
- `CHANGELOG.md`: 次バージョンに Added として記載。

## スコープ外(別 spec / 将来拡張)

- PII 検出の Presidio 委譲(spaCy コールドロード=常駐サーバ前提のため独立設計が必要)。
- detect-secrets / trufflehog 等の追加バックエンド。
- gitleaks のプロジェクト固有 `.gitleaks.toml`(`-c`)適用。
- バージョン番号の確定と CHANGELOG の版建ては実装プラン側で扱う。
