# safe-dev-hooks — Claude Codeで安全に開発するためのHooks集

[English README](README.md)

Claude Code の Hooks 機能を使って、エージェントの事故・暴走を防ぐための8本のHookをまとめた公開リポジトリです。世の中のベストプラクティス([出典はこちら](docs/best-practices.md))を踏まえて設計しています。

## 1. 概要 — 何を防ぐか

このHooks集は、Claude Codeでの開発中に起こり得る次の5種類のリスクを防ぐことを目的としています。

1. **破壊的コマンドの実行** — `rm -rf /`、`sudo rm`、保護ブランチへの force push、`mkfs`、`dd`、fork bomb、`DROP TABLE` 等
2. **機密情報の漏洩** — `.env` や秘密鍵など機密ファイルへの読取・編集・アクセス、シークレットの書き込み・外部送信
3. **品質の劣化** — lint/format を通さないままの編集の混入
4. **可視性の欠如** — 何が実行されたか・いつ許可待ちになったかが分からない
5. **MCPツール入出力を経由した情報漏洩** — 認証情報・PII・企業機微情報がMCP/Web経由で送受信されること

いずれも「悪意あるユーザーからの防御」ではなく、**エージェントの事故防止**を目的としています(詳細は [保証範囲](#6-保証範囲) と [docs/security-model.md](docs/security-model.md) を参照)。

## 2. クイックスタート

### 前提条件

- [`uv`](https://docs.astral.sh/uv/) が必須です(Python本体は uv が解決するため個別インストール不要)
- `exfil_guard` の semantic 判定(意味的なDLP判定)は Claude Code CLI(`claude` コマンド)が `PATH` にある場合のみ動作します。無い場合は自動的にスキップされ、正規表現ベースの他カテゴリのみで動作を継続します

### プラグインとして導入する

```
/plugin marketplace add wwwcojp/safe-dev-hooks
/plugin install safe-dev-hooks
```

これで8本のHookすべてが `hooks/hooks.json` の配線どおりに有効になります。

### 手動導入する(コピペで部分導入も可能)

```bash
git clone https://github.com/wwwcojp/safe-dev-hooks.git
```

[`examples/settings.full.json`](examples/settings.full.json)(全Hook)または [`examples/settings.minimal.json`](examples/settings.minimal.json)(bash_guard + secrets_guard のみの最小構成)の内容を `~/.claude/settings.json` にマージしてください。パス中の `$HOME/safe-dev-hooks` は実際に `git clone` したパスに置き換えます。関心事別モジュール構成のため、必要なHookだけを部分的に導入することもできます。

## 3. Hook一覧

| Hook | イベント / matcher | 動作 |
|------|--------------------|------|
| [bash_guard](docs/hooks/bash_guard.md) | PreToolUse / `Bash` | 回復不能系(`rm -rf /`、`sudo rm`、保護ブランチへのforce push、`mkfs`、`dd`、fork bomb、`DROP TABLE`等)は deny。グレー系(`git reset --hard`、`git clean -f`、再帰/強制の`rm`、`curl\|bash`等)は ask。`&&` `;` `\|\|` で連結されたコマンドも分解して検査 |
| [secrets_guard](docs/hooks/secrets_guard.md) | PreToolUse / `Read\|Edit\|Write\|Bash` | `.env`(`.env.example`等は許可)、`*.pem` / `id_rsa`、`~/.ssh/`、`~/.aws/credentials` 等への読取・編集・catを deny |
| [exfil_guard](docs/hooks/exfil_guard.md) | PreToolUse / `mcp__.*\|WebFetch\|WebSearch` | 外部送信引数のDLP検査(認証情報・PII・機密マーカー・カスタムパターン・semantic判定) |
| [exfil_output_scan](docs/hooks/exfil_output_scan.md) | PostToolUse / `mcp__.*\|WebFetch\|WebSearch` | 応答に含まれるシークレット・PIIの検出。警告(`additionalContext`)またはマスキング(`updatedToolOutput`)を設定で選択 |
| [quality_gate](docs/hooks/quality_gate.md) | PostToolUse / `Edit\|Write` | 編集ファイルへ lint/format を実行し、エラーは `decision:block` でClaudeに自己修正させる(warn/block設定可) |
| [secrets_scan](docs/hooks/secrets_scan.md) | PostToolUse / `Edit\|Write` | 書き込み内容からAWSキー・GitHubトークン・秘密鍵ブロック等を検出し block |
| [audit_log](docs/hooks/audit_log.md) | PreToolUse / PostToolUse / SessionStart / SessionEnd / Stop / `*` | 全ツール実行とセッション境界を JSONL で非同期記録 |
| [notify](docs/hooks/notify.md) | Notification | 許可待ち・アイドル時の通知(既定はターミナルベル、コマンド差し替え可) |

## 4. 設定

すべてのキーは任意です。設定ファイルが無くても、全ガードは安全側の既定値で動作します。プロジェクト直下の `.claude-hooks.json` が最優先、次に `~/.claude/claude-hooks.json`(個人既定値)、最後に同梱の `rules/*.json`(ビルトイン既定)がマージされます。

最小例:

```json
{
  "bash_guard": {
    "extra_deny": ["docker system prune"]
  },
  "exfil_guard": {
    "trusted_servers": ["mcp__internal-kb"]
  }
}
```

全スキーマ・3層マージの詳細・個人用/チーム用/高セキュリティの設定プリセットは [docs/configuration.md](docs/configuration.md) を参照してください。

## 5. 動作確認方法(初回は必須のウォームアップ手順)

各Hookスクリプトは `uv run --script` シバンで実行されるため、そのマシンでの初回実行時にPythonインタプリタの取得・インストールが走ることがあり、Hook自体のタイムアウト(10秒)を超えてしまう場合があります。導入直後に以下のコマンドを一度実行し、実際のHook呼び出しの外でこのセットアップを済ませておいてください。単なる任意の動作確認ではなく、必須のウォームアップ手順として扱ってください。

`bash_guard` が破壊的コマンドを検出して `deny` を返すことを、実際にHookスクリプトへ模擬イベントを流して確認できます。

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | uv run hooks/pre_tool_use/bash_guard.py
```

以下の内容を含むJSON(`permissionDecision: "deny"`)が1行で返れば正常です。

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "破壊的コマンドを検出: rm-root-or-home(deny層は設定で解除できません)"
  }
}
```

## 6. 保証範囲

Hooksは Claude Code の `disableAllHooks` 設定や Hook 自体の設定削除で無効化できるため、**悪意あるユーザーへの防御ではなく、エージェントの事故・暴走を防ぐための仕組み**です。deny層のパターンは permission mode に依らず決定論的にブロックされ、設定ファイルからは解除できませんが、正規表現の網羅性・semantic判定の確率性には既知の限界があります。保証すること/しないことの全体像は [docs/security-model.md](docs/security-model.md) を参照してください。

## 7. License / Contributing

- ライセンス: [LICENSE](LICENSE)(MIT)
- コントリビュート方法: [CONTRIBUTING.md](CONTRIBUTING.md)
- 変更履歴: [CHANGELOG.md](CHANGELOG.md)
