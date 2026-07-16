# notify: デスクトップ通知のフック統合 設計

- 日付: 2026-07-16
- 対象バージョン: 0.3.0
- ステータス: 承認済み

## 背景と目的

現状のデスクトップ通知は `examples/notify_wrapper.sh` を利用者が `notify.command` に絶対パスで接続する方式である。この方式は次の問題を実地で引き起こした。

- 設定ファイルの置き場所の誤解(Claude Code本体の `settings.local.json` に書いて動かない)
- `notify.command` がシェル非経由実行のため `$HOME` が展開されず、絶対パス直書きが必要
- 絶対パス直書きが `secrets_scan` の実ホームパス検査(`real-home-path`)と衝突

また「設定は調整のためであり、有効化のためではない」という本プロジェクトの設計原則(`docs/configuration.md`)に対し、デスクトップ通知だけが「設定しないと有効化されない」機能になっている。

本設計は wrapper のプラットフォーム判別ロジックを `notify.py` に統合し、ゼロ設定でデスクトップ通知が動く状態にする。

## 決定事項

| 論点 | 決定 |
|---|---|
| 既定動作 | `method: "auto"`(デスクトップ通知チェーン、不可ならベル)。ベル派には `method: "bell"` |
| 実装方式 | シェルロジックを `notify.py` 内へPython移植(1フック=1ファイル構成を維持) |
| wrapper と既存テスト | `examples/notify_wrapper.sh`・`tests/test_notify_wrapper.py` を削除 |
| 互換性 | `notify.command` は最優先のまま完全互換で維持 |

## 挙動仕様

設定スキーマ(`hooks/lib/config.py` の `DEFAULTS`):

```jsonc
"notify": {
  "enabled": true,
  "method": "auto",   // "auto" | "bell"(_ENUM_KEYS で検証、不正値は auto へフォールバック)
  "command": null     // 設定時は method より優先(従来互換)
}
```

`main()` の判定優先順位:

1. `enabled: false` → 何もしない
2. `command` 設定あり → 従来どおり `{message}` 置換で実行(タイムアウト10秒)。ベルは鳴らさない
3. `method: "bell"` → `{"terminalSequence": "\u0007"}` を返す
4. `method: "auto"`(既定)→ デスクトップ通知チェーンを順に試行し、最初に成功した時点で終了。全滅なら `terminalSequence` のベルへフォールバック

デスクトップ通知チェーン(現行wrapperと同一の判別・順序):

1. **WSL**: `WSL_DISTRO_NAME` 環境変数があるか `/proc/version` に `microsoft`(大文字小文字無視)を含み、かつ `powershell.exe` が `PATH` にある場合、WinRT API(`ToastNotificationManager`)でWindowsトースト
2. **Linuxデスクトップ**: `notify-send` があれば実行
3. **macOS**: `osascript` があれば `display notification`

各バックエンドは `shutil.which` で存在確認してから `subprocess.run`(タイムアウト5秒、出力捕捉)し、exit 0 を成功とする。失敗・例外・タイムアウトは次のバックエンドへ進む。

通知タイトルは `"Claude Code"` 固定、本文はイベントの `message`。

### wrapperからの意図的な変更点

- **`/dev/tty` 直書きフォールバックは移植しない**。これは「notify.py がコマンドの標準出力を捕捉して捨てるため、ベルを届けるには制御端末へ直接書くしかない」というwrapper固有の制約への回避策だった。統合後は notify.py 自身が出力を返せるため、素直に `terminalSequence` を使う。ベルは鳴るがメッセージテキストの端末表示は無くなる(メッセージはClaude Code本体のUIに表示されているため許容)。

## 実装構造

`hooks/notification/notify.py` 内に以下の小関数を追加する(約120行、外部依存なし):

| 関数 | 責務 | 戻り値 |
|---|---|---|
| `_is_wsl()` | `WSL_DISTRO_NAME` / `/proc/version` によるWSL判定 | bool |
| `_notify_windows_toast(title, message)` | PowerShell WinRTトースト | bool(成功可否) |
| `_notify_notify_send(title, message)` | notify-send実行 | bool |
| `_notify_osascript(title, message)` | osascript実行 | bool |
| `_notify_desktop(message)` | 上記チェーンの順次試行 | bool |
| `main()` | 優先順位分岐(挙動仕様の1〜4) | — |

インジェクション対策(現行wrapperと同一思想):

- PowerShellへはメッセージを `NOTIFY_TITLE` / `NOTIFY_MSG` 環境変数で渡し、`WSLENV` に両変数を追記してWSL境界を越えさせる。PowerShellスクリプト文字列への埋め込みはしない
- `notify-send` / `osascript` へはargvリスト渡し(シェル非経由)。osascript は `on run argv` 形式でAppleScriptへの文字列埋め込みを避ける

エラー処理: バックエンドの失敗はすべて握りつぶして次へ進み、最終的にベルへフォールバックする。通知フックがツール実行を妨げない現行の性質を維持する。

## テスト

`tests/test_audit_and_notify.py` に既存パターン(`load_hook` + monkeypatch)で追加:

1. `method: "bell"` → `terminalSequence` を返す
2. `method: "auto"` でバックエンド成功時 → `terminalSequence` を返さず、正しいargv・環境変数(`NOTIFY_MSG` 等)で `subprocess.run` が呼ばれる
3. `method: "auto"` で全バックエンド不在(`shutil.which` → None、`_is_wsl` → False)→ ベルへフォールバック
4. `command` 設定時 → デスクトップチェーンが呼ばれない(互換性)
5. `enabled: false` → 出力なし
6. `_is_wsl` の判定ロジック(環境変数系・/proc/version系)

`tests/test_config.py` に `notify.method` の不正値が `auto` へフォールバックすることを追加。

## 削除・ドキュメント・リリース

- 削除: `examples/notify_wrapper.sh`、`tests/test_notify_wrapper.py`
- `docs/hooks/notify.md`: auto/bell仕様へ全面改稿。`bash -c` による `$HOME` 展開の節は不要になるため削除し、動作確認手順を新仕様へ更新
- `docs/configuration.md`: スキーマに `method` を追加
- `README.md` / `README.ja.md`: Hook一覧の notify 行を更新
- `CHANGELOG.md`(0.3.0): 破壊的変更として明記 — ①既定動作がベル→autoに変わる ②`examples/notify_wrapper.sh` 削除(絶対パスで `notify.command` に指定していた利用者はリポジトリ/プラグイン更新でスクリプトが消えるため、`command` を外してautoへ移行するか自前スクリプトへ差し替えが必要)
- `.claude-plugin/plugin.json`: 0.3.0 へ

## 移行ガイド(CHANGELOGへ転記する内容)

- wrapper を `notify.command` で指定していた利用者: 設定から `notify.command` を削除するだけで `auto` が同等以上の動作をする
- ベルを維持したい利用者: `notify: {"method": "bell"}` を設定する
