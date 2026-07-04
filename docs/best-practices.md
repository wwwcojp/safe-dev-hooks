# ベストプラクティス

このHooks集を設計するにあたって調査した、Claude Code Hooksに関する公式ドキュメントおよび先行事例のまとめ。

## 出典

1. Claude Code 公式ドキュメント「Hooks」 — https://code.claude.com/docs/en/hooks
2. disler/claude-code-hooks-mastery — https://github.com/disler/claude-code-hooks-mastery
3. karanb192/claude-code-hooks — https://github.com/karanb192/claude-code-hooks
4. CodyLunders/claude-code-hooks-library — https://github.com/CodyLunders/claude-code-hooks-library

## 1. 公式ドキュメントからの知見(https://code.claude.com/docs/en/hooks)

- **`permissionDecision` の使い分け**: `PreToolUse` Hookは `hookSpecificOutput.permissionDecision` に `"allow"`/`"deny"`/`"ask"` を返すことで、ツール実行の許可を段階的に制御できる。単純な成功/失敗の2値ではなく「グレーな操作はユーザーに確認を委ねる」という中間状態を持てることが、危険度に応じた運用を可能にする。本Hooks集はこれを `bash_guard`/`secrets_guard`/`exfil_guard` の deny/ask 二段階設計として採用している。
- **exit codeよりJSON出力を優先する**: 単純な `exit 2` によるブロックは理由をClaudeに伝える手段が乏しい。`permissionDecisionReason`/`additionalContext`/`systemMessage` といったJSON出力フィールドを使うことで、なぜブロックされたか・何を修正すべきかをClaude自身にフィードバックできる。本Hooks集は全Hookでこの方式に統一している(`hooks/lib/hook_io.py`)。
- **matcherで範囲を絞る**: Hookの対象を `matcher`(ツール名の正規表現)で限定することで、無関係なツール呼び出しへの余計な検査コストを避けられる。本Hooks集は `hooks/hooks.json` で各Hookの対象を `Bash`、`Read|Edit|Write|Bash`、`mcp__.*|WebFetch|WebSearch` 等に絞っている。
- **`ask` でグレーゾーンをユーザーに委ねる**: 「危険だが正当な理由で必要になり得る操作」(`git reset --hard` 等)を一律denyにすると開発体験を損なう。`ask` によって最終判断をユーザーに残す設計が推奨されている。

## 2. disler/claude-code-hooks-mastery からの知見

- **uv single-file scriptsによる依存管理レス配布**: `# /// script` インラインメタデータでPython依存関係をスクリプト自体に埋め込み、`uv run` で実行することで、仮想環境構築やパッケージインストールの手間なくHookを配布できる。本リポジトリの全Hookスクリプトはこの形式(`#!/usr/bin/env -S uv run --script`)を採用している。
- **全イベントの監査ログ**: `PreToolUse`/`PostToolUse`/`SessionStart`/`SessionEnd`/`Stop` などライフサイクル全体をJSONLで記録し、後から何が起きたかを追跡可能にする。本Hooks集の `audit_log` はこのアプローチを踏襲している。

## 3. karanb192/claude-code-hooks ほかからの知見

- **1 Hook = 1関心事**: 破壊的コマンド検知・機密ファイル保護・品質チェックといった関心事を1つのモノリシックなスクリプトに詰め込まず、独立したスクリプトへ分割する。これにより個別に有効化・無効化・テスト・コピペ導入ができる。本リポジトリの `hooks/pre_tool_use/`・`hooks/post_tool_use/`・`hooks/audit/`・`hooks/notification/` というディレクトリ構成はこの原則を反映している。
- **コピペ可能な構成**: プラグインとしての一括導入だけでなく、`git clone` してから必要なスクリプトと `settings.json` のスニペットだけをコピーする部分導入を想定した構成にする(CodyLunders/claude-code-hooks-library も同様に、単体で完結するスクリプト集として配布している)。本Hooks集は `examples/settings.full.json`/`examples/settings.minimal.json` でこれをサポートしている。

## 4. 本リポジトリが採用した設計原則

上記の知見を踏まえ、本リポジトリでは以下を設計原則として採用した(詳細な決定経緯は [設計ドキュメントの決定事項ログ](../docs/superpowers/specs/2026-07-03-safe-dev-hooks-design.md) を参照)。

- **安全側の既定(secure by default)**: 設定ファイルが存在しなくても、全ガードがビルトインの安全側既定値で動作する。設定は「有効化」のためではなく「調整」のために存在する。
- **データ駆動ルール**: 危険パターン・シークレット形式・PII形式・機密マーカーは `rules/*.json` に集約し、コード変更なしでパターンを追加・拡張できる([CONTRIBUTING.md](../CONTRIBUTING.md) にルール追加手順を記載)。
- **deny層の設定不可侵**: 回復不能な破壊的操作(deny層)は設定ファイルから解除できないようにし、`ask` 層のみを利用者の裁量で調整可能にすることで、「うっかり全部allowしてしまう」事故を防ぐ。
- **fail-open + 可視化 / fail-close の使い分け**: ガード自体の異常でツール実行を止めない(fail-open)が、deny判定処理中の異常だけは安全側の `ask` に倒す(fail-close)。異常発生時は必ず `systemMessage` で利用者に伝える。
