# notify

## 目的

許可待ちやアイドル状態などの `Notification` イベントをユーザーに知らせる。既定(`method: "auto"`)で実行環境を自動判別してデスクトップ通知(WSL→Windowsトースト / Linuxデスクトップ→notify-send / macOS→osascript)を出し、使えない環境ではターミナルベルにフォールバックする。任意コマンドへの差し替え(`notify.command`)も可能。

## 対象イベント / matcher

- イベント: `Notification`(ツールmatcherは無く、Notificationイベント全般が対象)
- timeout: 10秒(`hooks/hooks.json`)

## 判定基準

このHookは `deny`/`ask`/`block` を返さない通知専用Hookである。優先順位:

1. `notify.enabled: false` → 何もしない
2. `notify.command` 設定あり → コマンド文字列中の `{message}` を通知メッセージ(シェルエスケープ済み)で置換し実行(タイムアウト10秒)。結果やエラーは無視され、ベルも鳴らさない
3. `notify.method: "bell"` → `{"terminalSequence": "\u0007"}`(ベル文字)を返す
4. `notify.method: "auto"`(既定)→ 下表のデスクトップ通知チェーンを順に試行し、最初に成功した時点で終了。全滅ならベルへフォールバック

| 順 | 環境判定 | 通知手段 |
|---|---|---|
| 1 | `WSL_DISTRO_NAME` があるか `/proc/version` に microsoft を含み、かつ `powershell.exe` が `PATH` にある | WinRT APIによるWindowsトースト |
| 2 | `notify-send` が `PATH` にある | Linuxデスクトップ通知 |
| 3 | `osascript` が `PATH` にある | macOS通知センター |

各バックエンドはタイムアウト5秒で実行され、失敗(非ゼロ終了・例外・タイムアウト)時は次へ進む。通知タイトルは `Claude Code` 固定。メッセージはPowerShellへは環境変数(`NOTIFY_TITLE`/`NOTIFY_MSG`、`WSLENV` でWSL境界を越える)、notify-send/osascriptへはargvで渡し、シェルやスクリプトへの文字列埋め込みは行わない。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `notify.enabled` | `true` | falseで本Hookを無効化 |
| `notify.method` | `"auto"` | `"auto"`=デスクトップ通知の自動判別(不可ならベル) / `"bell"`=常にターミナルベル |
| `notify.command` | `null` | 設定時は `method` より優先。`{message}` プレースホルダーが通知メッセージに置換される |

## 設定ファイルの置き場所

本Hookが読む設定ファイルは `~/.claude/claude-hooks.json`(個人・グローバル)とプロジェクト直下の `.claude-hooks.json` の2つ([設定リファレンス](../configuration.md)の3層マージを参照)。既定の `auto` はゼロ設定で動くため、通常は設定不要。`notify.command` を使う場合はマシン固有のパスを含みやすいためグローバル側を推奨する。

**注意**: Claude Code本体の `settings.json` / `settings.local.json` は本Hookの読み込み対象ではない。そこに `notify` キーを書いても無視される。

## 設定例

ターミナルベルに固定する:

```json
{
  "notify": {"method": "bell"}
}
```

独自コマンドへ差し替える(`notify.command` はシェルを介さず実行されるため、`$HOME` 等の展開が必要な場合は `bash -c` で包む):

```json
{
  "notify": {
    "command": "bash -c 'exec \"$HOME/bin/my-notify.sh\" \"$1\"' _ {message}"
  }
}
```

## 動作確認

```bash
# リポジトリ直下で実行。自環境のデスクトップ通知が表示されれば成功
printf '{"cwd": "%s", "message": "notify動作確認"}' "$PWD" | uv run hooks/notification/notify.py
```

出力が**空**であればデスクトップ通知が成功している。`{"terminalSequence": "\u0007"}` が出力された場合はデスクトップ通知が使えない環境で、ベルへフォールバックしている。

なお `Notification` イベントは `audit_log` の記録対象外のため、監査ログから本Hookの発火有無は確認できない([audit_log の既知の限界](audit_log.md)を参照)。

## 既知の限界

- カスタム `notify.command` の実行が失敗しても例外は握りつぶされ、フォールバックのベルも鳴らないため、通知が完全に無音になり得る(`method` 系は全滅時にベルへフォールバックする)。
- 通知の抑制・重複排除(デデュープ)・レート制限は無く、短時間に多数の `Notification` が発生した場合は通知もその都度実行される。
- バックエンドのタイムアウトは5秒固定、`command` のタイムアウトは10秒固定で設定不可。
- デスクトップ通知の成否は各コマンドの終了コードで判定するため、通知デーモン側で表示が抑制されるケース(集中モード等)は成功扱いになる。
