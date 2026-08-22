# Contributing

このリポジトリへのコントリビュートを歓迎します。ここではルールの追加方法とPRの前提条件をまとめます。

## 前提環境

- [`uv`](https://docs.astral.sh/uv/)(Python本体はuvが解決するため個別インストール不要)
- リポジトリのclone後、依存関係のインストールは不要(`uv run` が都度解決する)。開発用ツール(`pytest`/`ruff`)を明示的にインストールしたい場合は `uv sync` を実行する

### 検証ゲート(loop-hooks)

これはメンテナー向けのローカル開発環境の設定です(`~/loop-hooks` はメンテナーのローカルにのみ導入された未公開プラグインで、外部から取得できません)。外部コントリビューターは有効化手順を無視してかまいません — `uv run python scripts/verify.py quick` は単体でも動作するので、PR前に手動で実行するだけで十分です。

このリポジトリは loop-hooks(ローカルプラグイン `~/loop-hooks`)による「ターン終了時の検証ゲート」を前提に開発する。`.py`/`.json`/`pyproject.toml` を Edit/Write で変更したターンの終了時に `uv run python scripts/verify.py quick`(実ホームパスのリークチェック → `ruff check` → `pytest`。CI と同じコマンド・同じ順序)が強制され、失敗するとターンを終われない。結果は `.loop/evidence.jsonl`(gitignore)に1実行1行で記録される。

- 手動で回すとき: `uv run python scripts/verify.py quick`(約1秒)
- **テストを書いた/変えたタスクの完了条件**: `uv run python scripts/verify.py mutation`(mutmut でファイル別 mutation score を計測し、`.loop/mutation-baseline.json` を下回ると fail。上回れば自動更新。baseline は Git 追跡で PR の diff に出る)。対象は `pyproject.toml` `[tool.mutmut] only_mutate`。生き残りは `uv run mutmut results` / `uv run mutmut show <id>` で読み、厳密な期待値のテストで仕留める。真の等価変異のみ `# pragma: no mutate` を行単位で付け、理由をコメントする
- コミット前: `uv run python scripts/verify.py all`(quick → mutation)
- 設定は `.loop-hooks.json`。ゲート設定と `.loop/state.json` は `.claude-hooks.json` の `secrets_guard.write_protected_paths` で書き込み保護する(ユーザーが設定。`.claude-hooks.json` に `secrets_guard.write_protected_paths` が無ければ未設定 — 設計書 §6 の手順で設定する)。ゲートに詰まったらコードを直す — ゲート設定を変えて通さない
- 有効化はプロジェクト単位: `.claude/settings.local.json` の `enabledPlugins` に `"loop-hooks@loop-hooks": true`(設計: `docs/superpowers/specs/2026-08-22-loop-engineering-phase1-design.md`)

### ドッグフーディング時の注意(このリポジトリ自身のHooksを有効にして開発する場合)

`secrets_guard` の `write_protected` は、このインストール自身の `hooks/`/`rules/` ディレクトリ配下と `.claude-hooks.json`/`settings.json`/`settings.local.json`/`hooks.json` への改変(`Edit`/`Write`/Bash経由の変異コマンド)をdenyする(詳細: [docs/hooks/secrets_guard.md](docs/hooks/secrets_guard.md))。このプラグイン自身を有効にした状態でこのリポジトリを開発すると、まさに開発対象である `hooks/*.py` や `rules/*.json` の編集がこのHookによって遮断される。開発中は次のいずれかで回避すること。

- このプラグインを一時的に無効化する(`disableAllHooks` または `hooks.json` の除去)
- プラグインを別ディレクトリへ導入したインストールから、このリポジトリを編集対象にする(自己参照させない)

## 危険パターン・シークレット形式・PII形式の追加手順

危険コマンド・機密ファイルパス・シークレット形式・PII形式・機密マーカーは、すべて `rules/*.json` にデータとして定義されています(コード変更なしで拡張できる設計、[docs/best-practices.md](docs/best-practices.md) 参照)。新しいパターンを追加する場合は次の手順に従ってください。

1. **該当する `rules/*.json` に追記する**
   - 破壊的コマンド(即deny): `rules/bash_deny.json` に `{"name": "...", "regex": "..."}` を追加
   - 注意が必要なコマンド(ask): `rules/bash_ask.json` に同様の形式で追加
   - 機密ファイルパス: `rules/sensitive_paths.json` の `protected`(パターン)または `protected_dirs`(ディレクトリ)に追加
   - シークレット形式: `rules/secret_patterns.json` に追加(`validator` キーで `luhn`/`mynumber` 等の検証関数を指定可能。`hooks/lib/patterns.py` の `_VALIDATORS` を参照)
   - PII形式: `rules/pii_patterns.json` に追加
   - 機密マーカー文字列: `rules/confidential_markers.json` の `markers` 配列に追加
   - ルール名(`name`)は既存の命名(ケバブケース、英語)に合わせてください
2. **`tests/` に危険系・安全系のテストケースを追加する**
   - 対応するテストファイル(例: `tests/test_bash_guard.py`、`tests/test_secrets_guard.py`、`tests/test_exfil_guard.py`、`tests/test_secrets_scan.py` 等)に、以下の観点でケースを追加してください:
     - **危険系**: 新パターンが検出されて `deny`/`ask`/`block` が返ること
     - **安全系**: 似ているが該当しない入力(誤検知しやすいケース)が通過すること
     - 可能であれば **バイパス試行**(`&&`/`;`/`||` 連結、クォート・エスケープ、`$()` 置換、大文字小文字違い等)も追加する
3. **`uv run python scripts/verify.py quick` を実行し、全チェック(リーク・lint・テスト)が通ることを確認する**
4. 影響するドキュメント([README.ja.md](README.ja.md)/[README.md](README.md)、`docs/hooks/<hook名>.md`、必要なら `docs/security-model.md` の既知の限界)を更新する

## PRを出す前の確認事項

- [ ] `uv run python scripts/verify.py quick` が通る(= `pytest -q` green、`ruff check hooks tests scripts` クリーン、実ホームパスのリークなし。CI と同じ3チェック、`.github/workflows/ci.yml` 参照)
- [ ] テストを追加・変更した場合、`uv run python scripts/verify.py mutation` が通る(baseline を下回らない。等価変異の `# pragma: no mutate` には理由コメントがある)
- [ ] `rules/*.json` を変更した場合、対応するテストケース(危険系/安全系)を追加している
- [ ] 挙動を変更・追加した場合、README(日英)または `docs/` の該当箇所を更新している
- [ ] deny層のルールを追加する場合、それが「回復不能な操作」であり設定で解除すべきでないことを確認している(グレーな操作は `bash_ask.json`/`extra_ask` を使う)

## 質問・議論

設計上の意思決定は [docs/superpowers/specs/2026-07-03-safe-dev-hooks-design.md](docs/superpowers/specs/2026-07-03-safe-dev-hooks-design.md) の決定事項ログにまとめています。既存の設計判断に反する変更を提案する場合は、Issueで背景を共有してから着手してください。
