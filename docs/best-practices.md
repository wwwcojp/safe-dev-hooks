# ベストプラクティス

このHooks集を設計するにあたって調査した、Claude Code Hooksに関する公式ドキュメントおよび先行事例のまとめ。あわせて、初期実装後のハードニング(0.4.0)で得たルール設計上の知見と、その後の再調査(2026-07)で取り込んだ追加知見も記録する。

## 出典

1. Claude Code 公式ドキュメント「Hooks」 — https://code.claude.com/docs/en/hooks
2. disler/claude-code-hooks-mastery — https://github.com/disler/claude-code-hooks-mastery
3. karanb192/claude-code-hooks — https://github.com/karanb192/claude-code-hooks
4. CodyLunders/claude-code-hooks-library — https://github.com/CodyLunders/claude-code-hooks-library
5. paddo.dev「Claude Code Hooks: Guardrails That Actually Work」 — https://paddo.dev/blog/claude-code-hooks-guardrails/
6. Check Point Research「Caught in the Hook: RCE and API Token Exfiltration Through Claude Code Project Files」(CVE-2025-59536 / CVE-2026-21852) — https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/
7. ranthebuilder.cloud「Agentic Coding Hooks: Deterministic AI Guardrails」 — https://ranthebuilder.cloud/blog/agentic-coding-hooks-deterministic-ai-guardrails/

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

上記の知見を踏まえ、本リポジトリでは以下を設計原則として採用した(詳細な決定経緯は [設計ドキュメントの決定事項ログ](superpowers/specs/2026-07-03-safe-dev-hooks-design.md) を参照)。

- **安全側の既定(secure by default)**: 設定ファイルが存在しなくても、全ガードがビルトインの安全側既定値で動作する。設定は「有効化」のためではなく「調整」のために存在する。
- **データ駆動ルール**: 危険パターン・シークレット形式・PII形式・機密マーカーは `rules/*.json` に集約し、コード変更なしでパターンを追加・拡張できる([CONTRIBUTING.md](../CONTRIBUTING.md) にルール追加手順を記載)。
- **deny層の設定不可侵**: 回復不能な破壊的操作(deny層)は設定ファイルから解除できないようにし、`ask` 層のみを利用者の裁量で調整可能にすることで、「うっかり全部allowしてしまう」事故を防ぐ。0.4.0でこの原則を強化し、`enabled: false` でもdeny層は無効化されない(`bash_guard` はask層のみ無効化、`secrets_guard` はno-op化して `systemMessage` で通知)。Hooksの完全無効化の正規手段は `hooks/hooks.json` からの除去、または Claude Code 本体の `disableAllHooks` のみ。
- **ガード自身の自己保護**: ガードの設定ファイル(`.claude-hooks.json`・`settings.json` 等)と自インストールの `hooks/`・`rules/` への書込・変異コマンドを `secrets_guard` の write_protected でdenyする(0.4.0)。ガードを迂回する最短経路は「ガード自体の書き換え」であるため、これを塞ぐことでdeny層の不可侵性を実効化する。読取は妨げない。
- **fail-open + 可視化 / fail-close の使い分け**: ガード自体の異常でツール実行を止めない(fail-open)が、`bash_guard`・`secrets_guard` のdeny層判定中の異常だけは安全側の `ask` に倒す(fail-close)。異常発生時は必ず `systemMessage` で利用者に伝える。

## 5. 運用・ハードニングから得た知見(0.4.0)

初期実装後のハードニング(0.4.0)で、実装レビューをすり抜けたルール設計上の失敗クラスがいくつか見つかった。これらは [`.claude/rules/guard-rule-changes.md`](../.claude/rules/guard-rule-changes.md) に規約化している(詳細は [ハードニング設計ドキュメント](superpowers/specs/2026-07-18-hooks-hardening-design.md))。要点:

- **保護パスパターンをbasenameの裸置きにしない**: 裸の `settings.json` は全プロジェクトの `.vscode/settings.json` にまで一致し、解除不能なdenyで正当な編集を止め続ける。`.claude/` などでパススコープするか、解決済み絶対パスで判定する。
- **過剰検知は「安全側」ではない**: `2>/dev/null` のような日常的イディオムまでdenyすると、「denyは本当に破壊的な操作」という信頼が崩れる。誤ブロックはdeny層の信頼性を毀損するコストとして扱う。
- **字句回避の封じ込めは列挙でなく境界アンカーで**: バイパス変種(`rm -rf /.` 等)を個別列挙で塞ぐと取りこぼしが残る。固定幅の境界アンカーで網羅し、ReDoSを避けるため可変長の入れ子量化子を使わない。
- **テストは両方向を書く**: バイパス試行(false-negative)だけでなく、正当操作の誤ブロック(false-positive)も必ずテストする。過剰検知の見逃しは「バイパス例しかテストしていなかった」ことが原因だった。
- **denyを弱める変更はユーザー確認必須**: deny層の判定を緩める変更や新たな解除経路の追加は、機械的に適用せず必ず利用者の確認を挟む。

## 6. 再調査からの追加知見(2026-07)

初版執筆後の公式ドキュメントの拡張とエコシステムの動向を再調査した結果(出典5〜7)。

### 6.1 公式Hooks機能の拡張

公式ドキュメント(出典1)は初版調査時点から大幅に拡張されている。本Hooks集の設計に関係が深いもの:

- **イベントの大幅追加**: `UserPromptSubmit`・`PermissionRequest`/`PermissionDenied`・`SubagentStart`/`SubagentStop`・`PreCompact`/`PostCompact`・`ConfigChange`・`FileChanged` など、ライフサイクル全体を覆うイベントが追加された。本Hooks集が使う `PreToolUse`/`PostToolUse`/`SessionStart`/`SessionEnd`/`Stop`/`Notification` は引き続き中核だが、`ConfigChange`(設定ファイル変更の検知・ブロック)は write_protected による自己保護と補完関係にある将来候補。
- **ハンドラタイプの追加**: 従来の `command` に加え、`http`(HTTPエンドポイント)・`mcp_tool`(MCPツール呼び出し)・`prompt`(LLMによるyes/no判定)・`agent`(サブエージェント検証)が追加された。ただしLLMベースのフックは確率的であり、denyの根拠には適さない。ローカルで毎回同一に実行されるシェルスクリプトこそが真のガードレールの条件だという指摘(出典7)は、本Hooks集の「semantic判定はask専用・fail-open」([security-model](security-model.md))と同じ結論である。
- **`permissionDecision` の拡張**: `defer`(自フックは判断を保留し他のフックへ委ねる)と、ツール入力を書き換えて通す `updatedInput` が追加された。`updatedInput` は「危険なコマンドを安全な等価形へ書き換える」用途に使えるが、書き換えロジック自体が新たなバグ面・攻撃面になるため、本Hooks集では採用せず deny/ask の判定に徹する。
- **`if` フィールドとexec形式**: `"if": "Bash(git *)"` のような許可ルール構文でフックの起動自体を絞り込める。また `args` を指定するとシェルを介さないexec形式で起動され、シェルエスケープ起因の事故を避けられる(出典5も推奨)。
- **出力上限**: フック出力は10,000文字で切り詰められる。長大な `permissionDecisionReason`/`additionalContext` を前提にしない。

### 6.2 Hooks自体が攻撃面になる(CVE-2025-59536 / CVE-2026-21852)

2026年2月にCheck Point Researchが公表した脆弱性(出典6、公表前に修正済み)は、ガードレール機構そのものが侵入口になり得ることを示した:

- **CVE-2025-59536**: リポジトリ内 `.claude/settings.json` に定義されたフックが、信頼ダイアログの承認**前**に実行され、untrustedなリポジトリを開いただけで任意コマンドが実行された。
- **CVE-2026-21852**: プロジェクト設定で `ANTHROPIC_BASE_URL` を攻撃者のサーバへ上書きし、APIキーを平文で窃取できた。

教訓:

- **フック設定は実行可能コードとして扱う**: `settings.json`・`hooks.json` の変更はCI設定と同格にPRレビューの対象とする(出典7)。untrustedなリポジトリを開く前に、フックが定義されていないか確認する。
- **本Hooks集の位置づけ**: write_protected(0.4.0)は「エージェント自身によるガード改変」を塞ぐ層であり、上記のような「リポジトリ経由で持ち込まれるフック改変」への対策(信頼ダイアログ・人間のレビュー)とは別レイヤーで補完関係にある。どちらか一方では足りない。

### 6.3 コミュニティのガードレール設計知見

- **決定論的強制と指示の分離**: CLAUDE.mdやスキルへの指示は「依頼」であって保証ではない。必ず守られるべきことはフック(またはpermissions)に置き、指示は行動の誘導に使う——permissions(パターン遮断)・hooks(入力検査)・指示(誘導)の多層防御(出典5・7)。
- **warn→blockの段階導入**: 新しい検出パターンはまず警告として観測し、誤検知がないことを確認してからブロックへ昇格する(出典5)。本Hooks集のask層・`exfil_output_scan` のwarn/maskは同じ役割を持つ。
- **誤検知は保護全体を無効化させる**: 過剰なパターンは利用者に「ガードごと無効化する」ことを促す(出典5)。§5「過剰検知は安全側ではない」と同じ結論に独立に到達している。
- **フックは「決定的少数」に絞る**: 破壊的コマンド・シークレットと機微パス・必ず守るべき少数の基準のみをフック化する。過剰なフックはエージェントの性能と体験を劣化させる(出典7)。
- **フックが解決しないもの**: 未知の破壊パターン(バックアップが依然必須)、真の隔離(コンテナ/サンドボックスが必要)、複雑な検証ロジック。フックはsandboxの代替ではなく、多層防御の一層である(出典5)。
