# Contributing

このリポジトリへのコントリビュートを歓迎します。ここではルールの追加方法とPRの前提条件をまとめます。

## 前提環境

- [`uv`](https://docs.astral.sh/uv/)(Python本体はuvが解決するため個別インストール不要)
- リポジトリのclone後、依存関係のインストールは不要(`uv run` が都度解決する)。開発用ツール(`pytest`/`ruff`)を明示的にインストールしたい場合は `uv sync` を実行する

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
3. **`uv run pytest -q` を実行し、全テストが green であることを確認する**
4. **`uv run ruff check hooks tests` でlintエラーが無いことを確認する**
5. 影響するドキュメント([README.ja.md](README.ja.md)/[README.md](README.md)、`docs/hooks/<hook名>.md`、必要なら `docs/security-model.md` の既知の限界)を更新する

## PRを出す前の確認事項

- [ ] `uv run pytest -q` が green である
- [ ] `uv run ruff check hooks tests` がクリーンである(CI と同じコマンド、`.github/workflows/ci.yml` 参照)
- [ ] `rules/*.json` を変更した場合、対応するテストケース(危険系/安全系)を追加している
- [ ] 挙動を変更・追加した場合、README(日英)または `docs/` の該当箇所を更新している
- [ ] deny層のルールを追加する場合、それが「回復不能な操作」であり設定で解除すべきでないことを確認している(グレーな操作は `bash_ask.json`/`extra_ask` を使う)

## 質問・議論

設計上の意思決定は [docs/superpowers/specs/2026-07-03-safe-dev-hooks-design.md](docs/superpowers/specs/2026-07-03-safe-dev-hooks-design.md) の決定事項ログにまとめています。既存の設計判断に反する変更を提案する場合は、Issueで背景を共有してから着手してください。
