# Changelog

このプロジェクトの変更履歴は [Keep a Changelog](https://keepachangelog.com/ja/1.0.0/) の形式に従います。バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [0.3.0] - 2026-07-16

### Changed

- **破壊的変更** `notify`: デスクトップ通知(WSL→Windowsトースト / Linuxデスクトップ→notify-send / macOS→osascript)をHook本体へ統合し、既定動作をターミナルベルから自動判別(`notify.method: "auto"`)へ変更。デスクトップ通知が使えない環境では従来どおりベルへフォールバックする。ベルに固定したい場合は `notify.method: "bell"` を設定する。`notify.command` は従来どおり最優先で動作する(完全互換)。

### Removed

- **破壊的変更** `examples/notify_wrapper.sh`: 同等機能がHook本体へ統合されたため削除。`notify.command` に本スクリプトを絶対パスで指定していた場合、リポジトリ/プラグイン更新でスクリプトが消えるため、設定から `notify.command` を削除して既定の `auto` へ移行すること(同等以上の動作をする)。

## [0.2.0] - 2026-07-13

### Added

- `secrets_scan.custom_patterns`: 書き込み内容の検査にユーザー定義パターンを追加できる設定キー(`exfil_guard.custom_patterns` と同形式、ビルトインへマージ)。
- 実ホームパス混入防止の多層ガード: プロジェクト設定 `.claude-hooks.json`(`real-home-path` パターン)、プレースホルダー規約 `.claude/rules/no-personal-paths.md`、CIリークチェック(`ci.yml`)。

- `examples/notify_wrapper.sh`: `notify.command` に設定するデスクトップ通知ラッパー。実行環境を自動判別し、WSL(PowerShell/WinRTトースト)・Linuxデスクトップ(notify-send)・macOS(osascript)で通知、いずれも使えなければ `/dev/tty` へのベル出力(devcontainer等でも可聴)、制御端末も無ければ標準エラーへのベル出力にフォールバックする。

## [0.1.0] - 2026-07-05

### Added

- **Hooks 8本の初期実装**
  - `bash_guard`(PreToolUse / `Bash`): 破壊的コマンドのdeny/ask二段階ガード(`rules/bash_deny.json`・`rules/bash_ask.json`)
  - `secrets_guard`(PreToolUse / `Read|Edit|Write|Bash`): 機密ファイル(`.env`・秘密鍵・クラウド認証情報)への読取・編集・Bashアクセスの遮断(`rules/sensitive_paths.json`)
  - `exfil_guard`(PreToolUse / `mcp__.*|WebFetch|WebSearch`): MCP/Web外部送信引数のDLP検査(認証情報・PII・機密マーカー・カスタムパターン・semantic判定)
  - `exfil_output_scan`(PostToolUse / `mcp__.*|WebFetch|WebSearch`): MCP/Web応答からのシークレット・PII検出と警告/マスキング
  - `quality_gate`(PostToolUse / `Edit|Write`): 編集ファイルへのlint/format自動実行とClaudeへのフィードバック
  - `secrets_scan`(PostToolUse / `Edit|Write`): 書き込み内容からのシークレット検出とblock
  - `audit_log`(PreToolUse/PostToolUse/SessionStart/SessionEnd/Stop / `*`): 全イベントのJSONL非同期監査ログ
  - `notify`(Notification): 許可待ち・アイドル通知(ターミナルベル/カスタムコマンド)
- **設定システム**: `.claude-hooks.json`(プロジェクト)/ `~/.claude/claude-hooks.json`(グローバル)/ ビルトイン既定値の3層マージ、スキーマ検証と安全側フォールバック(`hooks/lib/config.py`)
- **データ駆動ルール定義**: `rules/bash_deny.json`、`rules/bash_ask.json`、`rules/sensitive_paths.json`、`rules/secret_patterns.json`、`rules/pii_patterns.json`、`rules/confidential_markers.json`、`rules/semantic_prompt.md`
- **配布**: `.claude-plugin/plugin.json` + `marketplace.json` によるプラグイン配布(`/plugin marketplace add` → `/plugin install safe-dev-hooks`)、`examples/settings.full.json` / `examples/settings.minimal.json` による手動導入スニペット
- **CI**: GitHub Actions で `ruff check` と `pytest` を実行(`.github/workflows/ci.yml`)
- **テスト**: 133件のpytestケース(危険系/グレー系/安全系、`&&`・`;`・`||` 連結やクォート・エスケープ等のバイパス試行、ReDoS回帰を含む)
- **ドキュメント**: README(日英)、Hookごとのリファレンス(`docs/hooks/*.md`)、設定リファレンス(`docs/configuration.md`)、セキュリティモデル(`docs/security-model.md`)、ベストプラクティス調査(`docs/best-practices.md`)、CONTRIBUTING.md

### Notes

- 実装・最終レビューにより設計時点からの変更がある(設計ドキュメントの決定事項ログ D12〜D16):
  - D12: `exfil_output_scan` のredactマスキングは1ルールにつき最大20件までの検出に限定(`MAX_FINDINGS_PER_RULE`)
  - D13: `secrets_guard` のBashトークン検査はパス形式のトークンのみを対象とし、裸のファイル名(拡張子なし)の直接アクセスは既知の限界として残っている
  - D14: `bash_guard` deny層の誤検知を除去(ホーム保護は `/home/<user>`・`/Users/<user>` 直下全体のみ、SQL系DROP/TRUNCATEはSQLクライアント実行文脈に限定)
  - D15: 設定のenum値(mode/action/categories.*)を検証し、不正値は安全側の既定値へフォールバック
  - D16: semantic判定のペイロード長ゲーティング(200文字未満スキップ)を撤廃し、長さに関わらず必ず判定(設定キー `min_payload_chars` は削除)
