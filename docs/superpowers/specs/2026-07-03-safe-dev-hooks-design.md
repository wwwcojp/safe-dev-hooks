# Claude Code 安全開発Hooks — 設計ドキュメント

- 日付: 2026-07-03
- ステータス: **ドラフト(セクション審議中)**
- 対象リポジトリ: claude-code-hooks(汎用公開リポジトリ)

## 1. 目的とスコープ

Claude Codeで安全に開発するためのHooks集を、世の中のベストプラクティスを踏まえて作成・公開する。防ぐリスク:

1. 破壊的コマンドの実行(rm -rf、force push、DROP TABLE 等)
2. 機密情報の漏洩(機密ファイルへのアクセス、シークレットの書き込み・外部送信)
3. 品質の劣化(lint/formatの自動適用)
4. 可視性の欠如(監査ログ・通知)
5. MCPツール入出力を経由した情報漏洩(認証情報・PII・企業機微情報)

## 2. 決定事項(経緯ログ)

| # | 決定 | 理由 |
|---|------|------|
| D1 | 汎用公開リポジトリとして開発 | 誰でも導入できるベストプラクティス集を目指す |
| D2 | 実装言語は Python(uv single-file scripts) | JSON解析・正規表現・テストが書きやすい。依存管理不要。公開Hooks集の事実上の標準(disler/claude-code-hooks-mastery 等) |
| D3 | 提供形態はプラグインと手動スニペットの両対応 | /plugin install での導入と、コピペでの部分導入の両方を許容 |
| D4 | プロジェクト依存設定は「設定ファイル + 自動検出」 | .claude-hooks.json で明示指定、未設定時は package.json / pyproject.toml から自動検出 |
| D5 | 危険操作は段階別 deny/ask | 回復不能な操作は即deny、グレーな操作はaskでユーザー判断 |
| D6 | アーキテクチャは関心事別モジュール(1スクリプト=1関心事) | 個別に有効化・コピペ・テスト可能。プラグイン/スニペット両対応に最適 |
| D7 | 判定は exit code ではなく JSON 出力(permissionDecision)で統一 | deny/ask の段階制御と理由表示が明示的にできる |
| D8 | mcp_guard は exfil_guard(外部送信ガード)に拡張し、**全MCPツール + WebFetch/WebSearch の入出力**を検査 | 検索クエリ・URL・ペイロード経由の漏洩と、応答経由でのシークレット流入の両方に対処 |
| D9 | exfil_guard は「検知時のみask」と「一律ask」を設定で選択可能 | 運用の厳格さを組織ポリシーに合わせられるようにする |
| D10 | 不要ファイル(ログ・キャッシュ・ローカル設定)は .gitignore で除外 | リポジトリ衛生の徹底 |

## 3. アーキテクチャ(承認済み)

```
claude-code-hooks/
├── .claude-plugin/plugin.json     # プラグインマニフェスト
├── hooks/
│   ├── hooks.json                 # プラグイン用Hook配線定義
│   ├── pre_tool_use/
│   │   ├── bash_guard.py          # 破壊的コマンドの deny/ask
│   │   ├── secrets_guard.py       # 機密ファイルへのアクセス遮断
│   │   └── exfil_guard.py         # 外部送信(MCP/Web)の入力検査
│   ├── post_tool_use/
│   │   ├── quality_gate.py        # 編集後の lint/format
│   │   ├── secrets_scan.py        # 書き込み内容のシークレット検出
│   │   └── exfil_output_scan.py   # MCP/Web応答の出力検査
│   ├── audit/
│   │   └── audit_log.py           # 全イベントのJSONL監査ログ
│   ├── notification/
│   │   └── notify.py              # 許可待ち・完了の通知
│   └── lib/                       # 共通処理(stdin解析・判定出力・設定読込)
├── rules/                         # パターン定義JSON(データ駆動)
├── tests/                         # pytest(各Hookへ模擬JSONを流す)
├── examples/settings.json         # 手動導入用スニペット
└── docs/                          # 文書一式
```

### 3.1 Hook一覧

| Hook | イベント / matcher | 動作 |
|------|--------------------|------|
| bash_guard | PreToolUse / `Bash` | 回復不能系(rm -rf /、sudo rm、保護ブランチへのforce push、mkfs、dd、fork bomb、DROP TABLE等)は deny。グレー系(git reset --hard、git clean -f、rm -rf <プロジェクト内>、curl\|bash等)は ask。`&&` `;` `\|\|` で連結されたコマンドも分解して検査 |
| secrets_guard | PreToolUse / `Read\|Edit\|Write\|Bash` | .env(.env.example等は許可)、*.pem / id_rsa、~/.ssh/、~/.aws/credentials 等への読取・編集・cat を deny |
| exfil_guard | PreToolUse / `mcp__.*\|WebFetch\|WebSearch` | 外部送信引数のDLP検査(詳細は3.2) |
| exfil_output_scan | PostToolUse / `mcp__.*\|WebFetch\|WebSearch` | 応答に含まれるシークレット・PIIの検出。警告(additionalContext)またはマスキング(updatedToolOutput)を設定で選択 |
| quality_gate | PostToolUse / `Edit\|Write` | 編集ファイルへ lint/format を実行し、エラーは decision:block でClaudeに自己修正させる(warn/block設定可) |
| secrets_scan | PostToolUse / `Edit\|Write` | 書き込み内容からAWSキー・GitHubトークン・秘密鍵ブロック等を検出し block |
| audit_log | PreToolUse+PostToolUse+Session系 / `*` | 全ツール実行を JSONL で非同期記録 |
| notify | Notification | 許可待ち・アイドル時の通知(既定はターミナルベル、コマンド差し替え可) |

### 3.2 exfil_guard(外部送信ガード)詳細

- **対象**: すべてのMCPツール(`mcp__.*`)+ 組み込みの WebFetch / WebSearch。入力(PreToolUse)と出力(PostToolUse)の両方向。
- **検査対象**: ツール引数全体(検索クエリ、URL、プロンプト、ペイロード)および応答本文。
- **検出カテゴリ**:
  1. 認証情報 — APIキー、トークン、秘密鍵ブロック
  2. 個人情報(PII) — メールアドレス、電話番号、クレジットカード番号(Luhn検証付き)、マイナンバー様の12桁数字
  3. 機密マーカー — 「社外秘」「部外秘」「取扱注意」「confidential」「internal only」等
  4. 組織固有パターン — 設定で定義するカスタム正規表現(社内ドメイン、コードネーム、顧客ID形式等)
- **動作モード(設定で選択)**:
  - `detect`(既定): 上記カテゴリを検知した場合のみ ask(検出内容を理由に提示)
  - `always`: 対象ツールの呼び出しを一律 ask(許可済みリスト登録サーバーを除く)
  - カテゴリごとに deny / ask / off の上書きも可能
- **除外制御**: 信頼するMCPサーバーの allowlist(検査スキップ)
- **既知の限界(文書化必須)**: 文脈依存のPII(人名等)は正規表現では検出不可。機械的に判定可能な形式+組織定義パターンを確実に止める設計とする。

## 4. 未確定(審議中)セクション

- 設定ファイル(.claude-hooks.json)のスキーマとルール定義形式
- 配布(プラグインマニフェスト・マーケットプレイス)・文書構成・多言語(日/英)方針
- テスト戦略・CI

---
*このドキュメントはセクション承認のたびに更新される。*
