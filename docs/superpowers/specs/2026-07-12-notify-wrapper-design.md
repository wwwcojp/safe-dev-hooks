# notify.command 用ラッパースクリプト設計

日付: 2026-07-12
対象: `examples/notify_wrapper.sh`(新規)、`docs/hooks/notify.md`(追記)

## 目的

`notify.command` に設定するだけで環境に応じたデスクトップ通知が出る、汎用ラッパースクリプトを examples として提供する。既定のターミナルベルでは気づきにくい許可待ち・アイドル通知を、OS ネイティブの通知に差し替え可能にする。

## インターフェース

- 通知メッセージを第1引数(`$1`)で受け取る。
- 設定例:

```json
{
  "notify": {
    "command": "bash /home/USER/safe-dev-hooks/examples/notify_wrapper.sh {message}"
  }
}
```

- 制約: `notify.command` は `shlex.split` + シェル無し `subprocess.run` で実行されるため、`$HOME` 等の環境変数やチルダは展開されない。**絶対パスで記載すること**。この注意はスクリプト冒頭コメントと `docs/hooks/notify.md` に明記する。

## 自動検出ロジック

上から順に判定し、最初に該当した手段で通知して終了する。

1. **WSL**(`$WSL_DISTRO_NAME` が非空、または `/proc/version` に `microsoft`): `powershell.exe -NoProfile` で WinRT API による Windows トースト通知(追加モジュール不要)。メッセージは PowerShell コマンド文字列に埋め込まず環境変数経由(`$env:...`)で渡し、インジェクションを回避する。
2. **Linux デスクトップ**: `notify-send` が存在すれば `notify-send "Claude Code" "$1"`。
3. **macOS**(`osascript` が存在): `on run argv` 形式でメッセージを引数渡しし、AppleScript 文字列埋め込みによるインジェクションを回避する。
4. **フォールバック**: 呼び出し元 `notify.py` は `capture_output=True` でコマンドの標準出力・標準エラーを捕捉して捨てるため、制御端末があれば `/dev/tty` へ直接ベル文字(`\a`)とメッセージを書き込む(devcontainer 等のデスクトップ通知が使えない環境でもベルを鳴らすため)。制御端末が無ければ標準エラーへ出力する。

## エラー方針

- 呼び出し元 `notify.py` は失敗を握りつぶすため(通知が無音になり得る既知の限界)、スクリプト側は `set -u` 程度にとどめ、各手段が失敗した場合は次の手段へフォールバックする。
- Hook のタイムアウトは10秒。`powershell.exe` 呼び出しが遅延する可能性があるため、ラッパー内で余計な待機はしない。

## テスト

- `tests/test_notify_wrapper.py`: フォールバック経路(PATH から通知コマンドを外した状態)で、制御端末がある場合は pty 経由で `/dev/tty` にベル文字とメッセージが届くこと、制御端末が無い場合は stderr に出力されること、いずれも終了コードが0であることを確認する。
- WSL トースト・notify-send・osascript の実発火は実機スモークテストで確認する(CI では検証しない)。

## 成果物

- `examples/notify_wrapper.sh`(実行権付き)
- `docs/hooks/notify.md` への設定例・パス展開注意の追記
- `tests/test_notify_wrapper.py`
