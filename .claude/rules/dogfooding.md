# 自リポジトリでの作業時の注意(ドッグフーディング)

このリポジトリ自身の Hooks が有効なまま作業すると、ガードが**自分の作業を止める**ことがある。想定内の挙動なので、以下で回避する(検査を無効化して回避しない)。

## よくある遮断と回避

1. **`hooks/`・`rules/` の Edit/Write が write_protected で遮断される** — 自インストールのフック/ルールは書込保護の対象。Bash 経由の python スクリプト書込(対象ファイルを読み、文字列置換して書き戻す)で回避するか、開発中はプラグインを無効化する。`tests/`・`docs/`・`.claude/rules/` は保護対象外なので、通常の Edit/Write でよい。

2. **Bash コマンドやコミットメッセージに危険コマンドの字面・実ホームパスを書くと遮断される** — `bash_guard` は `rm -rf //` のような字面に(コミットメッセージ内であっても)反応し、`secrets_scan` は書込内容中の実ホームパス(`real-home-path`)に反応する。コミットメッセージや説明文で危険コマンドを例示するときは表現を変える。パスは常にプレースホルダーを使う(`.claude/rules/no-personal-paths.md`)。

3. **ターン終了時に loop-hooks の検証ゲート(`scripts/verify.py quick`)で止められる** — `.py`/`.json`/`pyproject.toml` を Edit/Write で変更したターンは、終了時に leak → ruff → pytest が強制される。失敗したら**コードを直して再度終了する**。`.loop-hooks.json`・`.loop/state.json` は書込保護であり、ゲート設定を変えて通そうとしない(保護は「エージェントに回避できない」ことが設計)。

   **注意:** ゲートの dirty 判定は `Edit|Write` のみで、**項目 1 の Bash 経由の python 書込にはゲートが掛からない**。`hooks/`・`rules/` を変更する開発作業では、Bash 回避ではなく `CONTRIBUTING.md` の選択肢 1(プラグインを一時的に無効化して通常の Edit/Write を使う)を優先すること。Bash で書いた場合は自分で `uv run python scripts/verify.py quick` を回す。

## 参照

開発環境セットアップ全般は `CONTRIBUTING.md` を参照。ガード自体を変更する場合は `.claude/rules/guard-rule-changes.md`。
