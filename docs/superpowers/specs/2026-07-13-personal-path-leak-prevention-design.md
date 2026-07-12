# 実ユーザー名・実ホームパスの混入防止(多層防御)設計

日付: 2026-07-13
背景: 2026-07-12、リネーム作業のplan/specに実ホームパス(`/home/<実ユーザー名>`)がコミットされ、`git filter-repo` + force push による履歴書き換えが必要になった。2026-07-10 に同種の除去を実施済みだったにもかかわらず再発した。

## 教訓

1. 一度きりの履歴清掃は再発を防がない。規約の明文化と自動ゲートを清掃と同時に導入すべきだった。
2. 既存フックの検知クラス漏れ: `secrets_scan` はシークレット・PIIを検知するが「実ユーザー名・実ホームパス」は対象外。さらに `custom_patterns` は `exfil_guard` 専用で、書き込み経路にはユーザー定義パターンを追加できない(製品ギャップ)。
3. 「個人情報を記載しない」という抽象的ポリシーは機能しない。検証可能な具体度(禁止パターンとプレースホルダー規約)で書く。
4. 防止は多層で: 方針(rules)→書き込み時(フック)→コミット時(CI)。

## 対策(3層)

### 層1: 方針の明文化

- `.claude/rules/no-personal-paths.md` を新設(Git管理)。内容:
  - リポジトリ内ファイルに実ユーザー名・実ホームパスを書かない。
  - プレースホルダー規約: ドキュメントは `$HOME` または `/home/USER`、テストフィクスチャは `/home/alice`。
- `CLAUDE.md` の「個人情報」に実ユーザー名・実ホームパスが含まれることを明記し、ルールファイルを参照。未追跡だった `CLAUDE.md` をコミットする。

### 層2: 書き込み時ブロック(secrets_scan 拡張)

- `secrets_scan` に `custom_patterns` 設定を追加(`exfil_guard.custom_patterns` と同形式: `[{"name": ..., "regex": ...}]`)。ビルトインの `secret_patterns.json` にマージして検査し、検出時は従来どおり block。
- `config.py` の DEFAULTS に `secrets_scan.custom_patterns: []` を追加。
- 不正な設定エントリで例外が出た場合は既存の `fail_open` に乗る(exfil_guard と同挙動)。
- プロジェクト設定 `.claude-hooks.json`(新設・Git管理)に汎用パターンを設定:
  - `/(home|Users)/` の直後にプレースホルダー(`USER`・`alice`・`user`)以外の英数字名が続くものを検出。実ユーザー名そのものは設定ファイルに埋め込まない。

### 層3: CIゲート

- `.github/workflows/ci.yml` に追跡ファイル全体への `git grep -P` チェックを追加。層2と同じ汎用正規表現で、ヒットしたら fail。手動編集・Bash heredoc 等、フックを通らない書き込み経路をカバーする。

### 補助

- `docs/superpowers/2026-07-05-handoff.md` に本件(再混入→履歴書き換え→防止策導入)を追記。
- `docs/configuration.md`・`docs/hooks/secrets_scan.md`・`CHANGELOG.md` を更新。

## テスト

- `tests/test_secrets_scan.py`: custom_patterns 追加時に該当書き込みが block されること、未設定時は従来挙動、不正エントリ(regex欠落等)で fail_open すること。
- `tests/test_config.py`: DEFAULTS への `custom_patterns` 追加とマージ動作。
- CIゲートの正規表現は、現行の追跡ファイル(`/home/alice` フィクスチャ、`/home/USER`・`/home/<user>` ドキュメント表記)に誤検知しないことをローカルで実測確認する。

## 採用しなかった案

- Claude Code ネイティブフック(`.claude/settings.json` + $USER 動的導出): 即効性はあるがこのプロジェクト専用の対策に留まる。本リポジトリは Hook 製品自体であり、判明したギャップを製品側で埋める方が横展開できるため secrets_scan 拡張を採用。
