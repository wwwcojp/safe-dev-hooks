# 自リポジトリでの作業時の注意(ドッグフーディング)

このリポジトリ自身の Hooks が有効なまま作業すると、ガードが**自分の作業を止める**ことがある。想定内の挙動なので、以下で回避する(検査を無効化して回避しない)。

## よくある遮断と回避

1. **`hooks/`・`rules/` の Edit/Write が write_protected で遮断される** — 自インストールのフック/ルールは書込保護の対象。Bash 経由の python スクリプト書込(対象ファイルを読み、文字列置換して書き戻す)で回避するか、開発中はプラグインを無効化する。`tests/`・`docs/`・`.claude/rules/` は保護対象外なので、通常の Edit/Write でよい。

2. **Bash コマンドやコミットメッセージに危険コマンドの字面・実ホームパスを書くと遮断される** — `bash_guard` は `rm -rf //` のような字面に(コミットメッセージ内であっても)反応し、`secrets_scan` は書込内容中の実ホームパス(`real-home-path`)に反応する。コミットメッセージや説明文で危険コマンドを例示するときは表現を変える。パスは常にプレースホルダーを使う(`.claude/rules/no-personal-paths.md`)。

3. **ターン終了時に loop-hooks の検証ゲート(`scripts/verify.py quick`)で止められる** — `.py`/`.json`/`pyproject.toml` に**未コミットの変更があるターン**は、終了時に leak → ruff → pytest が強制される。失敗したら**コードを直して再度終了する**。`.loop-hooks.json`・`.loop/mutation-baseline.json` は `.claude-hooks.json` の `write_protected_paths` で書込保護する対象(ユーザーが設定。`.claude-hooks.json` に `secrets_guard.write_protected_paths` が無ければ未設定なので、第1段階 spec §6 / 第2段階 spec §6 の手順で設定する)。保護の有無にかかわらず、ゲート設定を変えて通そうとしない。write_protected は Edit/Write と Bash の変異トークンを塞ぐ**予防層**であり、「うっかりゲートを直してしまう」経路を閉じるためのもの — インタプリタレベルの書込(python heredoc で開いて書く等)は素通りする既知の限界があり、最終的な判定者は CI とブランチレビュー(人間)。mutation の baseline は `scripts/verify.py mutation` 自身が向上時に書き換える(それ以外の経路で下げない)。対象ファイルを `only_mutate` から外すと baseline との不一致で fail するので、対象の縮小はユーザーが baseline も手で外す。mutation は CI で回らないので、`scripts/verify.py` 自身(killed 判定・ラチェット)の改変はブランチレビューでしか検出できない。

   **変更検出について(2026-08-24 更新):** loop-hooks は `PostToolUse(Edit|Write)` で dirty を記録する方式をやめ、**`watch` に一致する未コミット変更の内容ハッシュ**で発火するようになった。書いた経路を問わないので、**項目 1 の Bash 経由の python 書込もゲート対象**になる(実測確認済み)。状態はリポジトリ外(`$CLAUDE_PLUGIN_DATA/state/` または `~/.cache/loop-hooks/state/`)に移り、`<repo>/.loop/state.json` は読まれない。ゲートは `Stop` に加えて `SubagentStop`・`TeammateIdle` でも走る。

   **注意:** プラグインの入口ファイルが動くと、**稼働中のセッションは古い登録を掴んだまま**になり(フック定義はセッション開始時のスナップショット)、ゲートが無言で走らなくなる。`~/loop-hooks` を更新したら Claude Code を再起動すること。再起動までは `uv run python scripts/verify.py quick` を自分で回す。

## 参照

開発環境セットアップ全般は `CONTRIBUTING.md` を参照。ガード自体を変更する場合は `.claude/rules/guard-rule-changes.md`。
