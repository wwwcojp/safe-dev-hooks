# notify

## 目的

許可待ちやアイドル状態などの `Notification` イベントをユーザーに知らせる。既定ではターミナルベルを鳴らし、任意コマンドへの差し替えも可能。

## 対象イベント / matcher

- イベント: `Notification`(ツールmatcherは無く、Notificationイベント全般が対象)
- timeout: 10秒(`hooks/hooks.json`)

## 判定基準

このHookは `deny`/`ask`/`block` を返さない通知専用Hookである。

- `notify.command` が設定されている場合: コマンド文字列中の `{message}` を `event.message`(シェルエスケープ済み)で置換し、`subprocess.run` で実行する(タイムアウト10秒)。実行結果やエラーは無視される。この場合ターミナルベルは鳴らさない。
- `notify.command` が未設定(既定値 `null`)の場合: `hookSpecificOutput` は使わず、`{"terminalSequence": "\u0007"}`(ベル文字)を返してターミナルへベルを送る。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `notify.enabled` | `true` | falseで本Hookを無効化 |
| `notify.command` | `null` | 通知時に実行するコマンド文字列。`{message}` プレースホルダーが通知メッセージに置換される。`null` ならターミナルベル |

## 設定ファイルの置き場所

本Hookが読む設定ファイルは `~/.claude/claude-hooks.json`(個人・グローバル)とプロジェクト直下の `.claude-hooks.json` の2つだけである([設定リファレンス](../configuration.md)の3層マージを参照)。`notify.command` はマシン固有の絶対パスを含むため、**グローバル側(`~/.claude/claude-hooks.json`)に書くことを推奨**する。プロジェクトの `.claude-hooks.json` はコミット対象のため、実ホームパスを書くとリポジトリへの個人情報混入になる(本リポジトリのように `secrets_scan` の `real-home-path` パターンを設定している場合は書き込み自体がブロックされる)。

**注意**: Claude Code本体の `settings.json` / `settings.local.json` は本Hookの読み込み対象ではない。そこに `notify` キーを書いても無視される。

## 設定例: デスクトップ通知ラッパー

[`examples/notify_wrapper.sh`](../../examples/notify_wrapper.sh) を使うと、実行環境の自動判別(WSL→Windowsトースト / Linuxデスクトップ→notify-send / macOS→osascript)でデスクトップ通知に差し替えられる。いずれも使えない環境(devcontainer等)では、制御端末があれば `/dev/tty` へ直接ベル文字とメッセージを書き込む(本Hookはコマンドの標準出力・標準エラーを捕捉して捨てるため、`/dev/tty` 直接書き込みでないとベルが届かない)。制御端末も無ければ標準エラーへ出力する。

```json
{
  "notify": {
    "command": "bash -c 'exec \"$HOME/safe-dev-hooks/examples/notify_wrapper.sh\" \"$1\"' _ {message}"
  }
}
```

`notify.command` は `shlex.split` + シェル無しの `subprocess.run` で実行されるため、そのままでは `$HOME` やチルダは展開されない。上の例は `bash -c` で包むことでシェル側に `$HOME` を展開させ、実ホームパスの直書きを避けている(通知メッセージは `$1` として位置引数で渡るため、メッセージ内容がシェル解釈されることもない)。絶対パスを直書きしても動作はするが、設定作業をClaude Code自身に依頼した場合に `secrets_scan` の実パス検査へ抵触するため、`$HOME` 展開形式を推奨する。

## 動作確認

```bash
# 1. ラッパー単体の確認(デスクトップ通知が表示されるか)
bash "$HOME/safe-dev-hooks/examples/notify_wrapper.sh" テスト通知

# 2. Hook経由のE2E確認(設定が読まれているか。リポジトリ直下で実行)
printf '{"cwd": "%s", "message": "E2Eテスト"}' "$PWD" | uv run hooks/notification/notify.py
```

2の出力が**空**であれば `notify.command` 分岐が実行されている(通知が表示されれば成功)。`{"terminalSequence": "\u0007"}` が出力された場合は設定が読まれていない(置き場所とキー名を確認すること)。

なお `Notification` イベントは `audit_log` の記録対象外のため、監査ログから本Hookの発火有無は確認できない([audit_log の既知の限界](audit_log.md)を参照)。

## 既知の限界

- カスタム `notify.command` の実行が失敗(コマンド不在・非ゼロ終了・タイムアウト等)しても例外は握りつぶされ、フォールバックのベルも鳴らないため、**通知が完全に無音になり得る**。
- 通知の抑制・重複排除(デデュープ)・レート制限は無く、短時間に多数の `Notification` が発生した場合はコマンドやベルもその都度実行される。
- コマンドのタイムアウトは10秒固定で設定不可。
- Hook自体が例外を出した場合の扱いはコード上明示のフォールバックが無い(他の判定系Hookのような `fail_open`/`fail_close` の分岐は無く、通知処理自体が単純なため通常は例外が発生しない設計)。
