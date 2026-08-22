# Loop Engineering 第1段階(決定論的ゲート) 設計書

作成日: 2026-08-22
前提: なし(開発体制への追加。プラグイン `safe-dev-hooks` の機能ではない)
参照元: `~/rakuten-optimizer/docs/superpowers/specs/2026-08-19-loop-engineering-design.md`(原設計)、
`~/news-collector/docs/superpowers/specs/2026-08-20-verification-roadmap-design.md`(Python/uv 環境への移植例)

---

## 1. 何を作るか、なぜ作るか

**エージェントが回避できない検証インフラを、このリポジトリにも入れる。**

このリポジトリは `CONTRIBUTING.md` に「PR前に `pytest` と `ruff` を通す」と書いてあるが、強制はCIだけで、
ローカルでは自己申告である。他PJ(rakuten-optimizer / news-collector)で実証済みの
「ターン終了時(Stop)に検証コマンドを強制し、失敗したらターンを終われない」仕組みを同じ形で導入する。

方向づけ(CLAUDE.md・Skills)と強制(フック・実行可能な検証物)を分ける:

> CLAUDE.md = policy / orientation
> Hooks + executable artifacts = enforcement

## 2. 全体構成 — 3つの構成要素

| 構成要素 | 置き場所 | 責務 |
|---|---|---|
| verify ランナー | このリポジトリ `scripts/verify.py` | 検証の実体。lint・テスト等を束ねて実行し、結果を evidence として残す |
| loop-hooks プラグイン | `~/loop-hooks`(ローカル marketplace、登録済み) | フック層。per-repo 設定の verify コマンドを Stop で呼び、失敗ならターンを終わらせない。**検証の中身を知らない**。**本設計ではプラグインを変更しない** |
| per-repo 設定 | このリポジトリのルート `.loop-hooks.json` | どのコマンドがゲートか、どのファイル変更がゲート対象か |

「何を検証するか」はリポジトリ側、「回避できない仕組み」はプラグイン側。

## 3. 動作フロー(loop-hooks の契約。再掲)

1. **PostToolUse(Edit|Write)**: 編集ファイルが `watch` に一致し `ignore` に一致しなければ `.loop/state.json` に dirty を記録。検証は走らせない
2. **Stop**: dirty のときだけ `gate.command` を実行。成功 → dirty を消して終了。失敗 → `decision: block` + 失敗出力を返し、ターンを終わらせない
3. **無限ループ防止**: `stop_hook_active` 再入時は失敗しても警告だけ出して通す(dirty は残るので次のターン終了時に再びゲート)
4. `.loop-hooks.json` が無いリポジトリでは何もしない(オプトイン)

## 4. verify ランナー `scripts/verify.py`

`uv run python scripts/verify.py <stage>`。stdlib のみで書く(uv の依存解決に乗せない。`hooks/` と同じ流儀)。

| ステージ | 中身 | 想定実行者 |
|---|---|---|
| `quick` | ①`leak`: 実ホームパス漏洩チェック(CI と同じ `git grep -nP` と正規表現) → ②`lint`: `uv run ruff check hooks tests scripts` → ③`tests`: `uv run pytest -q` | **Stop フック(毎ターン)**・コミット前 |

- `quick` は CI(`.github/workflows/ci.yml`)の3ステップと**同じコマンド・同じ順序**にする。
  「ローカルで通れば CI も通る」を保つため、片方を変えたらもう片方も変える(CI 側は生コマンドのまま。CI から `verify.py` を呼ぶ構成にはしない)
- 最初に失敗したチェックで打ち切る。失敗コマンドの stdout+stderr 末尾 2000 字を stderr に出す。
  Stop フックはそれをそのまま Claude への修復指示として転送する
- `all` 等の追加ステージは第2段階以降で足す(YAGNI)
- 実測(2026-08-22): pytest 241件 0.6秒・ruff 0.1秒・leak は git grep。**合計約1秒**。絞り込みは不要

### 4.1 evidence `.loop/evidence.jsonl`

実行ごとに1行追記。フォーマットは loop-hooks README の契約どおり:

```json
{"ts":"2026-08-22T12:34:56.789Z","rev":"ff34449+dirty","stage":"quick","pass":false,"checks":[{"name":"leak","ok":true,"ms":12},{"name":"lint","ok":true,"ms":90},{"name":"tests","ok":false,"ms":640}]}
```

「検証した」という主張を、自己申告ではなく機械の記録にするためのもの。

**他PJとの相違: `.loop/` は gitignore する。** このリポジトリは公開リポジトリ+CI があり、
evidence はローカルの作業証跡、最終判定者は CI。公開履歴にローカル実行ログを載せない。
(既存の `*.jsonl` ignore に加え `.loop/` を明示。将来 mutation baseline を入れるときは
`!.loop/mutation-baseline.json` で例外追跡する)

## 5. per-repo 設定 `.loop-hooks.json`

```json
{
  "gate": {
    "command": "uv run python scripts/verify.py quick",
    "timeout_sec": 120,
    "watch": ["*.py", "*.json", "pyproject.toml"],
    "ignore": [".loop/*", ".superpowers/*"]
  }
}
```

- `*.py` = `hooks/`・`tests/`・`scripts/`、`*.json` = `rules/*.json`・`hooks/hooks.json`・`.claude-plugin/*.json`
- `*.md` は対象外(他PJと同じ。実ホームパスは `secrets_scan` が書込時点で止める。ドキュメント単独の編集でゲートは掛けない)
- パターンは `fnmatch`(`*` は `/` をまたぐ)。リポジトリ相対パスに対して照合。`ignore` 優先

## 6. 保護(ユーザー手動 2件)

以下は `safe-dev-hooks` 自身の `write_protected` により**エージェントは変更できない**(Edit/Write/Bash いずれも deny)。
設計として「エージェントに回避できない」ことが目的なので、ユーザーが手で行う。

### 6.1 必須: `.claude-hooks.json` にゲート設定の書込保護を追加

赤いゲートに詰まったエージェントが、`.loop-hooks.json` の `command` を `true` に差し替える・
`.loop/state.json` を `{"dirty": false}` に書き換えるといった「ゲート自体を修理する」事故経路を塞ぐ
(rakuten-optimizer で同型の事故が過去2回。申し送り §1(b))。

現在の `.claude-hooks.json` に `secrets_guard` キーを追加する(`secrets_scan` はそのまま):

```json
{
  "secrets_scan": {
    "custom_patterns": [
      {
        "name": "real-home-path",
        "regex": "/(home|Users)/(?!USER\\b|alice\\b|user\\b)[A-Za-z_][A-Za-z0-9._-]*"
      }
    ]
  },
  "secrets_guard": {
    "write_protected_paths": [".loop-hooks.json", "*.loop/state.json"]
  }
}
```

確認: `python3 -c "import json; json.load(open('.claude-hooks.json'))"`。
`*.loop/state.json` は `fnmatch` の先頭一致(`.loop/state.json` と `<repo>/.loop/state.json` の両表記)を覆うため(dev-lessons §14)。

### 6.2 必須: プラグインの有効化

marketplace `loop-hooks` は `~/.claude/settings.json` の `extraKnownMarketplaces` に登録済み。
有効化はプロジェクト単位で `.claude/settings.local.json`(gitignore 済み)に追記する:

```json
"enabledPlugins": {
  "loop-hooks@loop-hooks": true
}
```

フックはセッション開始時に読み込まれるため、**新しいセッションから有効**。

## 7. CI・ドキュメント

- `.github/workflows/ci.yml`: `ruff check` の対象に `scripts` を追加(`uv run ruff check hooks tests scripts`)。他は変更なし
- `CONTRIBUTING.md`: 「PR前の確認事項」に `uv run python scripts/verify.py quick`(CI と同じ3チェックを1コマンドで)を追記し、ゲートの存在を書く
- `.claude/rules/dogfooding.md`: 「Stop ゲートで止まったら直す。`.loop-hooks.json`・`.loop/state.json` は書込保護で、ゲート設定を変えて通さない」を追記
- `README`(日英)は変更しない。ゲートはこのリポジトリの**開発体制**であり、プラグインのユーザー向け機能ではない
- `.gitignore`: `.loop/` を追加

## 8. このリポジトリ固有の注意点

### 8.1 ドッグフーディング規約との干渉(最重要)

`.claude/rules/dogfooding.md` は `hooks/`・`rules/` の編集(自インストールの write_protected で Edit/Write が deny される)を
**Bash 経由の python スクリプト書込で回避する**よう案内している。一方 loop-hooks の dirty 判定は
PostToolUse の `Edit|Write` のみで、**Bash 経由の変更にはゲートが掛からない**(rakuten 申し送り §3 の既知の境界)。

つまり、まさに開発対象である `hooks/*.py`・`rules/*.json` を Bash 経由で変更してターンを終えても、ゲートは走らない。

扱い:
- 開発時は `CONTRIBUTING.md` の選択肢1(**プラグインを一時的に無効化**して通常の Edit/Write を使う)を優先する旨を
  `dogfooding.md` に明記する。Edit/Write で編集すればゲートが掛かる
- Bash 書込の dirty 化(`Bash` の PostToolUse で書込先を推定する)は loop-hooks 側の課題として記録に留め、本設計では扱わない
- CI が最終の網であることは変わらない

### 8.2 既存の Stop フックとの共存

`safe-dev-hooks` 自身の Stop フック(`audit_log.py`、`async: true`、ブロックしない)と `stop_hook_active` を共有するが、
ブロックしないので実害なし。ブロックする Stop フックを将来このリポジトリに足す場合は再確認する。

### 8.3 フック本体が壊れると「素通り」側に倒れる

Claude Code は exit 1 を非ブロックとして扱うため、loop-hooks 本体がクラッシュするとゲートは掛からない
(可用性側に倒れる設計)。ゲートが効いていないように見えたら、まずこの可能性を疑う。

## 9. ハーネス自身のテスト `tests/test_verify.py`

| 観点 | 中身 |
|---|---|
| 失敗で非ゼロ・打ち切り | 落ちるダミーコマンド(`python -c "import sys; sys.exit(1)"`)を含むチェック列で、戻り値が False、evidence に `pass:false`、後続チェックが `checks` に現れない |
| 成功で pass | 全チェック成功で True、evidence に `pass:true` と全チェックの `ok:true` |
| 失敗出力の転送 | 失敗コマンドの出力末尾が stderr に出る |
| 未知ステージ | 非ゼロ終了(`SystemExit`) |

テストは `subprocess` で `uv run` を呼ばない(`run_stage` にチェック列と `repo_root` を注入する)。
`scripts/` は `tests/conftest.py` の `sys.path` に追加して import する(`hooks/` と同じ方式)。
mutation の受け入れ条件(打ち切り判定を外してテストが落ちることを確認)はここにも適用する。

## 10. 作らないもの

- **第2〜4段階**(次節にロードマップとしてのみ記す)
- **loop-hooks 本体の変更**(Bash 書込の dirty 化を含む)
- **CI からの `verify.py` 呼び出し**(CI は生コマンドのまま。二重の定義源にしない代わりに §4 の「同じコマンド・同じ順序」を守る)
- **evidence のローテーション**(gitignore なので肥大化しても履歴を汚さない。困ったら消す)
- **`rules/sensitive_paths.json` の既定 `write_protected` への `.loop-hooks.json` 追加**(全ユーザーへの製品変更。per-repo 設定で足りる。必要になれば別 spec)

## 11. 次の段階(ロードマップ。着手は別タスクで判断)

news-collector の移植順序を踏襲する。各段階は独立に価値が出るので、段階ごとに運用して次を判断する。

- **第2段階 mutation 自動化**: mutmut のスパイク → `verify.py mutation` ステージ + `.loop/mutation-baseline.json` のラチェット(下回ったら fail。Stop には入れない。テストを書いたタスクの完了条件にする)。
  対象の第1候補は `hooks/lib/patterns.py`・`hooks/lib/config.py`(純粋関数でテストも速い)。baseline の書込保護追加はユーザー手動
- **第3段階 PBT**: hypothesis を dev 依存に追加。候補: `config.load` は任意の不正 JSON/型で raise しない(0.6.x の修正の不変条件)、
  bash_guard の deny 判定はコマンドの前後空白・`;`/`&&` 連結に対して単調(連結で deny が消えない)
- **第4段階 静的解析・アーキテクチャ**: `hooks/lib/` は `hooks/pre_tool_use/` 等のエントリポイントを import しない、
  `scripts/verify.py` は `hooks/` に依存しない(ハーネスと対象の分離)を pytest で機械判定

## 12. リスクと未確定事項

| 項目 | 内容 | 扱い |
|---|---|---|
| Bash 書込の素通り | §8.1 | dogfooding.md の運用で緩和。loop-hooks 側の課題として記録 |
| `quick` の所要時間 | 約1秒 | 問題なし。テストが大幅に増えたら再計測 |
| セッション異常終了 | dirty が残る/消える整合 | dirty はセッションを跨いで残るので次セッションの Stop で回収される(loop-hooks の設計) |
| サブディレクトリを cwd にしたセッション | `.loop-hooks.json` が見つからず no-op | 既知の境界。このリポジトリはルートで作業する |
