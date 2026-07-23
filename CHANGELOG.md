# Changelog

このプロジェクトの変更履歴は [Keep a Changelog](https://keepachangelog.com/ja/1.0.0/) の形式に従います。バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [0.6.0] - 2026-07-23

### Added
- 秘密検出の任意バックエンドとして gitleaks 委譲を追加(`scanners.gitleaks`: `auto`/`off`/`docker`)。内蔵 patterns を floor として残す union(加算)方式で、deny 保証を弱めずカバレッジを拡張。
- `scanners.gitleaks_image`(Docker イメージ)・`scanners.gitleaks_config`(`.gitleaks.toml` 指定、未指定時は `<cwd>/.gitleaks.toml` 自動)を追加。

### Changed
- `exfil_guard`/`secrets_scan`/`exfil_output_scan` の秘密検出を共有集約点 `scanners.scan_secrets` 経由に変更(内蔵挙動は不変・gitleaks 不在時は従来同等)。

## [0.5.0] - 2026-07-20

### Added

- **`config_guard`(新Hook / `ConfigChange`)** — セッション中の設定ファイル変更(user/project/local/policy/skills)を `systemMessage` で通知し、変更後に `disableAllHooks: true` が有効な場合は追加警告する検知専用フック。write_protected(予防層)が見えない経路 — インタプリタレベルの書込・外部プロセス・人間の手による編集 — での設定変更を可視化する。ブロックはしない(`disableAllHooks` という正規の解除手段、および人間自身の設定編集を妨げないため。warn→block 段階導入の原則)。設定キー `config_guard.enabled`(警告専用のため `false` で完全無効化可)。`audit_log` も `ConfigChange` に配線し監査ログへ記録する。

### Changed

- **`secrets_guard`: write_protected に `.mcp.json`・`.claude.json` を追加** — MCPサーバ定義の `command` は任意コマンド実行経路であり、フック定義(`settings.json`)と同格の改変標的となるため(Claude Code のプロジェクト設定ファイル群を攻撃面とした CVE-2025-59536 / CVE-2026-21852 の教訓。`docs/best-practices.md` セクション6.2)。
- **`secrets_guard`: `curl`/`wget` の出力フラグによる書込を write_protected の検査対象に追加** — `-o`(バンドル末尾 `-fsSLo`・密着引数 `-oFILE` を含む)/`--output`、wget の `-O`/`--output-document`(`=` 連結形式を含む)の引数トークンを保護対象と照合し、ダウンロードによる設定/フックファイルの上書きを deny する。wget の `-o`/`--output-file`(ログ書込)・`-a`/`--append-output`(ログ追記)も検査対象。セグメント内に curl/wget が混在する場合は両ツールのフラグ集合を適用。出力フラグの引数のみを照合するため、読取用途の `curl`(URL に保護ファイル名を含む場合など)や `/tmp` への保存は妨げない。`curl -O`・裸の `wget URL` のようにファイル名がURL側から決まる形式は対象外(既知の限界としてドキュメント化)。

## [0.4.0] - 2026-07-19

### Changed

- **`bash_guard`: force-push保護をrefspecまで拡張** — `bash_guard.protected_branches`(既定 `["main","master","develop","release","production"]`)を新設し、`--force`/`-f` 形式に加えて `+` refspec形式(例: `git push origin +HEAD:main`)も検出。refspecの送信先(コロン右側)が保護対象ブランチかどうかで判定するため、`git push origin +main:feature`(ローカルの`main`を保護対象外のリモートブランチへ送る操作)はdenyにならない。
- **`bash_guard`: `rm`/`find` のdeny降格対策** — `rm-root-or-home` に `rm -rf /.`・`rm -rf /..` のような末尾ドット回避を追加。新規 `find ... -delete` / `-exec rm` ルール(root/homeが対象なら `find-delete-root` でdeny、それ以外は `find-delete` でask)。同一コマンド内の定数代入(`T=/; rm -rf $T`)を展開してからdeny判定するようになった(動的な値は展開できず引き続きaskどまり)。
- **`bash_guard`: curl/wgetの機微データ送信をask検査** — データ送信フラグ(`-d`/`--data*`/`-F`/`--form`/`-T`/`--upload-file`等)と機微オペランド(環境変数、コマンド置換、`sensitive_paths.json` の保護ファイル名)を同時に含む `curl`/`wget` をaskへ倒す。`exfil_guard`(MCP/WebFetch/WebSearch専用)ではカバーされないbash経由の外部送信の隙間を埋める(`scp`/`rsync`/`nc` は対象外)。
- **`bash_guard`/`secrets_guard`: deny層の`enabled:false`免疫** — `bash_guard.enabled: false` はask層のみを無効化し、deny層は常に動作するよう修正(従来は無効化の余地があった)。`secrets_guard.enabled: false` はdeny層に対して完全にno-opとし、`systemMessage` で「enabled:false でも deny 層を無効化できません」と通知する。Hooksの完全無効化は `hooks/hooks.json` からの除去、または Claude Code 本体の `disableAllHooks` のみが正規の手段である。
- **`secrets_guard`: write_protectedで設定/フック自体の改変を遮断** — `.claude-hooks.json`・`settings.json`・`settings.local.json`・`hooks.json`、およびこのインストール自身の `hooks/`/`rules/` ディレクトリへの `Edit`/`Write`、および `Bash` 経由の変異コマンド(リダイレクト・`dd of=`・`rm`/`mv`/`cp`/`sed -i`/`tee`/`truncate`/`ln`/`install`)をdenyする。読取(`Read`)は妨げない。新設定キー `secrets_guard.write_protected_paths`。

### Docs

- `docs/security-model.md`/`docs/configuration.md`/`docs/hooks/bash_guard.md`/`docs/hooks/secrets_guard.md` を上記の変更に合わせて更新。特に `configuration.md` の「denyは`enabled:false`で外せる」という誤った記述を訂正。
- `CONTRIBUTING.md` にドッグフーディング時の注意(このリポジトリ自身のHooksを有効にしたまま `hooks/`/`rules/` を編集しようとするとwrite_protectedに遮断される旨)を追記。

### Fixed

- `bash_guard`: `protected_branches` に空リスト `[]` を明示した場合、従来は `["main","master"]` へ暗黙フォールバックしていたのを、「保護ブランチ無し」として force-push の deny 規則を生成しないよう修正(空の正規表現による全ブランチ誤検知も回避)。
- `bash_guard`: curl/wget 外部送信askの `allow` 照合を、ask層と同じくクォート除去後のセグメントに対して行うよう統一。
- `hook_io.finalize`: 設定エラー通知が既存の `systemMessage`(例: `secrets_guard` の enabled:false 注記)を上書きしていたのを、両者を連結して保持するよう修正。

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
