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

## 設定例: デスクトップ通知ラッパー

[`examples/notify_wrapper.sh`](../../examples/notify_wrapper.sh) を使うと、実行環境の自動判別(WSL→Windowsトースト / Linuxデスクトップ→notify-send / macOS→osascript / いずれも無ければ標準エラーへベル出力)でデスクトップ通知に差し替えられる。

```json
{
  "notify": {
    "command": "bash /home/USER/safe-dev-hooks/examples/notify_wrapper.sh {message}"
  }
}
```

**注意**: `notify.command` は `shlex.split` + シェル無しの `subprocess.run` で実行されるため、`$HOME` やチルダは展開されない。スクリプトのパスは必ず絶対パスで記載すること。

## 既知の限界

- カスタム `notify.command` の実行が失敗(コマンド不在・非ゼロ終了・タイムアウト等)しても例外は握りつぶされ、フォールバックのベルも鳴らないため、**通知が完全に無音になり得る**。
- 通知の抑制・重複排除(デデュープ)・レート制限は無く、短時間に多数の `Notification` が発生した場合はコマンドやベルもその都度実行される。
- コマンドのタイムアウトは10秒固定で設定不可。
- Hook自体が例外を出した場合の扱いはコード上明示のフォールバックが無い(他の判定系Hookのような `fail_open`/`fail_close` の分岐は無く、通知処理自体が単純なため通常は例外が発生しない設計)。
