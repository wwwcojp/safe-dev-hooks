# Claude Code 安全開発Hooks — 設計ドキュメント

- 日付: 2026-07-03
- ステータス: **全セクション承認済み(2026-07-03)**
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
| D11 | 機密マーカーが無い機微情報は semantic カテゴリ(LLM意味的判定)で検出。実装はヘッドレスClaude(`claude -p`、小型モデル)呼び出し | 正規表現では文脈依存の機微情報を検出できない。ヘッドレス方式なら設定ファイル(trusted_servers・モード)と完全統合でき、判定プロンプトもカスタマイズ可能。APIキー不要(Claude Code認証を流用) |
| D12 | scan_text はルールごとに全マッチを収集(同一文字列は重複排除、1ルール上限20件)。当初計画の「1ルール1件」から実装時レビューで変更 | redactマスキングで2件目以降の異なるシークレットが漏れる実害があるため(2026-07-04 レビュー指摘・ユーザー承認) |
| D13 | secrets_guard のBashトークン検査は「パス形式のトークン」のみ対象(glob含みは除外、`/`含む・`.`/`~`始まり・拡張子風の`.`含みが対象) | 全トークン検査では `grep credentials` や `find -name "*.pem"` 等の検索コマンドまで解除不能denyになり実用性を損なうため(2026-07-04 レビュー指摘・ユーザー承認)。裸のファイル名(拡張子なし)の直接catは検知漏れとなる既知の限界として security-model.md に明記する |
| D14 | bash_guard deny層の誤検知除去: rm-system-dir から home を除外し「ユーザーホーム直下全体のみ」の rm-home-root を新設(深いサブパスはask層へ)。SQL系(DROP/TRUNCATE)はSQLクライアント(psql/mysql/mariadb/sqlite3/sqlcmd)実行文脈に限定 | `rm -rf /home/user/proj/node_modules` や `grep "DROP TABLE"`・コミットメッセージ内SQL文字列まで解除不能denyになることを最終レビューで実測確認(2026-07-05)。回復不能操作のブロックという意図を保ったまま誤検知のみ除去。GUIツール等の未列挙クライアント経由のSQLは検知対象外(既知の限界として文書化) |
| D15 | 設定のenum値(mode/action/categories.*)を検証し、不正値は既定値へフォールバック+_errorsで可視化 | タイポ(例 categories.credentials: "denny")でdenyが黙ってaskに降格する実害を最終レビューで確認(2026-07-05)。セクション型サニタイズ(4.3)と合わせスキーマ検証を完全化 |
| D16 | semantic判定のペイロード長ゲーティング(既定200文字未満スキップ)を撤廃し、長さに関わらず必ず判定する。設定キー `semantic.min_payload_chars` も削除 | 短い検索クエリにも機微情報(単発のID・固有名詞等)が乗り得るため、検出漏れ防止を優先するユーザー判断(2026-07-05)。レイテンシ・コスト増は既知のトレードオフとして文書化し、抑制手段は categories.semantic: "off" / trusted_servers に委ねる |

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
| audit_log | PreToolUse / PostToolUse / SessionStart / SessionEnd / Stop / `*` | 全ツール実行とセッション境界を JSONL で非同期記録 |
| notify | Notification | 許可待ち・アイドル時の通知(既定はターミナルベル、コマンド差し替え可) |

### 3.2 exfil_guard(外部送信ガード)詳細

- **対象**: すべてのMCPツール(`mcp__.*`)+ 組み込みの WebFetch / WebSearch。入力(PreToolUse)と出力(PostToolUse)の両方向。
- **検査対象**: ツール引数全体(検索クエリ、URL、プロンプト、ペイロード)および応答本文。
- **検出カテゴリ**:
  1. 認証情報 — APIキー、トークン、秘密鍵ブロック
  2. 個人情報(PII) — メールアドレス、電話番号、クレジットカード番号(Luhn検証付き)、マイナンバー(12桁+チェックデジット検証付き。単なる12桁数字では誤検知するため)
  3. 機密マーカー — 「社外秘」「部外秘」「取扱注意」「confidential」「internal only」等
  4. 組織固有パターン — 設定で定義するカスタム正規表現(社内ドメイン、コードネーム、顧客ID形式等)
  5. 意味的判定(semantic) — 機密マーカーが無くても機微と思われる情報(人事・給与・顧客情報・未公開の事業情報等)をLLMで判定。ヘッドレスClaude(`claude -p`、既定はHaiku)に判定プロンプトを投げ、「機微の可能性 + 理由」を受け取って ask に変換
- **semanticカテゴリの制約**:
  - 判定は確率的なため **ask 専用**(deny には使わない)。検出理由を提示しユーザーが最終判断
  - ゲーティング: 正規表現カテゴリで既に ask/deny が確定していればスキップ(ペイロード長によるスキップはD16で撤廃 — 長さに関わらず必ず判定する)
  - 判定プロンプトは rules/semantic_prompt.md に置きカスタマイズ可能
  - ヘッドレス呼び出しはツール実行ではないためHookの再帰発火は起きない
- **動作モード(設定で選択)**:
  - `detect`(既定): 上記カテゴリを検知した場合のみ ask(検出内容を理由に提示)
  - `always`: 対象ツールの呼び出しを一律 ask(許可済みリスト登録サーバーを除く)。askは下限であり、カテゴリ検査で deny 判定が出た場合は deny のまま(降格しない)
  - カテゴリごとに deny / ask / off の上書きも可能
- **除外制御**: 信頼するMCPサーバーの allowlist(検査スキップ)
- **既知の限界(文書化必須)**: 文脈依存の機微情報(人名等)は正規表現では検出不可。semantic カテゴリが補完するが確率的であり、検出漏れはあり得る。「正規表現+組織定義パターンで機械的に判定可能なものは確実に止め、それ以外は semantic でベストエフォート検出」という保証レベルを security-model.md に明記する。

## 4. 設定ファイルとルール定義(承認済み)

### 4.1 設定の優先順位(上が強い)

1. プロジェクトの `.claude-hooks.json`(コミットしてチーム共有可)
2. グローバルの `~/.claude/claude-hooks.json`(個人既定値)
3. 同梱の `rules/*.json`(ビルトイン既定)

### 4.2 `.claude-hooks.json` スキーマ

全キー任意。未指定は安全側の既定値で動作する(設定は「調整」のためであり「有効化」のためではない)。

```jsonc
{
  "bash_guard": {
    "enabled": true,
    "extra_deny": ["docker system prune"],
    "extra_ask": [],
    "allow": ["rm -rf node_modules"]        // ask層のみ解除可。deny層は設定では解除不可
  },
  "secrets_guard": {
    "enabled": true,
    "protected_paths": ["config/secrets/**"],
    "allow_paths": [".env.template"]
  },
  "exfil_guard": {
    "enabled": true,
    "mode": "detect",                        // "detect"=検知時のみask / "always"=一律ask
    "categories": {
      "credentials": "deny",
      "pii": "ask",
      "confidential_markers": "ask",
      "custom": "ask",
      "semantic": "ask"                      // deny | ask | off(semanticはask/offのみ)
    },
    "semantic": {
      "model": "haiku"                       // ヘッドレス判定に使うモデル(長さゲーティングはD16で撤廃)
    },
    "custom_patterns": [
      { "name": "社内ドメイン", "regex": "[\\w.-]+\\.example\\.co\\.jp" }
    ],
    "trusted_servers": ["mcp__internal-kb"]  // 検査スキップ(alwaysモードでも除外)
  },
  "exfil_output_scan": { "enabled": true, "action": "warn" },  // "warn" | "redact"
  "quality_gate": {
    "enabled": true,
    "mode": "block",                         // "block"=Claudeに修正させる / "warn"=注記のみ
    "commands": { "*.py": ["ruff check {file}"] }  // 未指定なら自動検出
  },
  "secrets_scan": { "enabled": true },
  "audit_log": { "enabled": true, "path": ".claude/logs" },
  "notify": { "enabled": true, "command": null }
}
```

### 4.3 設計原則

- **安全側の既定**: 設定ファイルが無くても全ガードが既定値で動く
- **denyの解除は不可**: `allow` で解除できるのは ask 層のみ。回復不能系 deny を外すには Hook 自体の無効化しかない(文書に明記)
- **データ駆動**: 危険パターン・シークレット形式・PII形式は `rules/*.json` に集約し、コード変更なしで拡張可能。`extra_*` / `custom_patterns` はビルトインへマージ
- **quality_gateの自動検出**: `commands` 未指定時は拡張子と `pyproject.toml`(ruff/black)、`package.json`(eslint/prettier/biome)、`Cargo.toml`(clippy/rustfmt)等から推定。検出不能なら何もしない
- **設定自体の検証**: 起動時にスキーマ検証。不正時は `systemMessage` で通知し安全側の既定値で継続

## 5. 配布(承認済み)

- **プラグイン**: `.claude-plugin/plugin.json`(名前: `safe-dev-hooks`、semver管理)+ `hooks/hooks.json` で全Hookを配線。スクリプト参照は `${CLAUDE_PLUGIN_ROOT}`。`.claude-plugin/marketplace.json`(公式規定の配置。当初の「リポジトリ直下」は誤りで2026-07-05修正)を置き、`/plugin marketplace add <GitHubリポジトリ>` → `/plugin install safe-dev-hooks` で導入可能にする
- **手動導入**: `examples/settings.json` に settings.json 用スニペット(全部入り/最小構成の2種)。`git clone` + コピペで部分導入可能
- **前提条件**: `uv` のみ(Python本体はuvが解決)。semantic判定は Claude Code CLI(`claude`)の存在を前提とし、見つからなければ自動スキップして他カテゴリのみで動作する

## 6. 文書化方針(承認済み)

日英バイリンガル(README.md=英語 / README.ja.md=日本語。docs/ 配下は日本語を正とし、英訳は将来課題として明記)。

| 文書 | 内容 |
|------|------|
| `README.md` / `README.ja.md` | 概要、クイックスタート(プラグイン/手動)、Hook一覧表 |
| `docs/hooks/<hook名>.md` | Hookごとのリファレンス: 目的、検査内容、判定基準(deny/ask)、設定キー、既知の限界 |
| `docs/configuration.md` | `.claude-hooks.json` 全スキーマと設定例(個人/チーム/高セキュリティの3プリセット) |
| `docs/security-model.md` | 脅威モデル: 保証すること/しないこと(正規表現の限界、semantic判定の確率性、`disableAllHooks` による無効化可能性等) |
| `docs/best-practices.md` | 調査したベストプラクティスの出典付きまとめ(公式ドキュメント・先行リポジトリ) |
| `CHANGELOG.md` / `CONTRIBUTING.md` | Keep a Changelog 形式の変更履歴、ルール追加のコントリビュート手順 |

設計判断は本ドキュメントの決定事項ログ(セクション2)に追記し続ける。

## 7. テスト戦略・CI(承認済み)

- **pytest**: 各Hookへ模擬イベントJSON(`tests/fixtures/`)をstdin経由で流し、判定JSON出力を検証。ケース構成:
  - 危険系(denyが返ること)/グレー系(askが返ること)/安全系(無判定で通ること)
  - **バイパス試行**: `&&` `;` `||` 連結、クォート・エスケープ、`$()` 置換、絶対パス表記ゆれ、大文字小文字
- **semantic判定はモック**: ヘッドレスClaude呼び出しはテストではスタブ化。実疎通は手動スモークテスト手順として `docs/` に文書化
- **CI**: GitHub Actions で `ruff check` + `pytest`(uvセットアップ)。`rules/*.json` のスキーマ検証も含め、パターン追加PRを機械検証する

## 8. エラーハンドリング方針

- Hookスクリプト自体の例外は **fail-open + 可視化**: クラッシュでツール実行を止めない(exit 0)が、`systemMessage` で「ガードが動作しなかった」ことを必ず通知する。ただし bash_guard / secrets_guard の deny 層判定中の例外のみ **fail-close**(安全側に倒して ask を返す)
- タイムアウト: 各Hookは軽量に保ち(10s)、quality_gate は 90s、exfil_guard は semantic 判定を含むため 60s を hooks.json で明示(当初案の「quality_gate 60s」は実装時に90sへ変更)
- 監査ログ書き込み失敗は無視(開発を止めない)

---
*このドキュメントはセクション承認のたびに更新される。実装計画は writing-plans スキルで別途作成する。*
