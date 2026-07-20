# safe-dev-hooks — Claude Codeで安全に開発する

[English README](README.md)

AIエージェントの事故や暴走 — 破壊的コマンド・シークレット漏洩・未レビューの編集 — を未然に止める、[Claude Code Hooks](https://docs.claude.com/en/docs/claude-code/hooks) 9本のコレクションです。設定なしですぐ動く安全側の既定値つき。世の中の[ベストプラクティス](docs/best-practices.md)を踏まえて設計しています。

## 何を防ぐか

Claude Code での開発中に起こり得る、次の5種類の事故を防ぎます。

1. **破壊的コマンド** — `rm -rf /`、`sudo rm`、保護ブランチへの force push、`mkfs`、`dd`、fork bomb、`DROP TABLE` など。
2. **シークレット漏洩** — `.env`・秘密鍵・クラウド認証情報の読取・編集・外部送信。
3. **品質の劣化** — lint/format を通さないまま入る編集。
4. **記録の欠如** — 何が実行されたか・いつ許可待ちになったかが残らない。
5. **MCP/Webツール経由の漏洩** — 認証情報・PII・企業機微情報が、ツール引数で外へ出たり、ツール応答で入ってきたりすること。

これは**悪意あるユーザーへの防御ではありません** — Claude Code の設定を触れる人はHooksをOFFにできます。目的は、エージェント自身が高くつく失敗をしないよう止めることです。何をどこまでカバーするかは[保証範囲](#保証すること--しないこと)を参照してください。

## 導入

### 1. プラグインを追加する

```
/plugin marketplace add wwwcojp/safe-dev-hooks
/plugin install safe-dev-hooks
```

これで9本すべてが `hooks/hooks.json` の配線どおりに有効になります。必要なのは `PATH` 上の [`uv`](https://docs.astral.sh/uv/) だけ — Python本体は uv が用意するので、個別インストールは不要です。一部のHookだけ動かしたい場合は、下の[手動導入](#手動導入部分導入)を使ってください。

### 2. 動作確認 — 導入直後に一度、必須

> [!IMPORTANT]
> **Hooksはフェイルオープンです。** `uv` が無い・エラーになると、各Hookは非ゼロ終了し、Claude Code は警告するだけで処理を続行し、**全ガードが黙ってno-op(素通し)になります**。通常利用では気づけないので、導入後に必ず下のコマンドを一度実行してください(`uv` の初回インタプリタ取得 — Hookの10秒タイムアウトを超え、同じくフェイルオープンし得る — のウォームアップも兼ねます)。

模擬イベントを `bash_guard` に流し、破壊的コマンドが `deny` されることを確認します。

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | uv run hooks/pre_tool_use/bash_guard.py
```

`"permissionDecision": "deny"` を含むJSONが1行返れば正常です。

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "破壊的コマンドを検出: rm-root-or-home(deny層は設定で解除できません)"}}
```

### 手動導入・部分導入

必要なHookだけ選びたい、あるいはプラグイン機構を使いたくない場合は、クローンして設定スニペットを `~/.claude/settings.json` にマージします。

```bash
git clone https://github.com/wwwcojp/safe-dev-hooks.git
```

- **全Hook:** [`examples/settings.full.json`](examples/settings.full.json) をマージ。
- **最小構成(`bash_guard` + `secrets_guard` のみ):** [`examples/settings.minimal.json`](examples/settings.minimal.json) をマージ。

マージするスニペット中の `$HOME/safe-dev-hooks` を、実際にクローンした場所に置き換えます。各Hookは自己完結したモジュールなので、一部だけの導入で問題ありません。

**`uv` を使わない場合:** 各Hookスクリプトの依存はPython標準ライブラリのみなので、Python 3.10 以上があれば動きます。マージするコマンドの `uv run` を `python3` に置き換えてください — ただしシステムの `python3` が 3.10 未満だと全Hookが失敗(フェイルオープン=素通し)するため、上の動作確認を必ず再実行してください。この方法は手動導入専用です(プラグインは `uv` 前提の配線)。

> `exfil_guard` の任意の semantic 判定(LLMによるDLP判定)は、`claude` CLI が `PATH` にある場合のみ動作します。無ければその判定だけスキップされ、正規表現ベースの検査は動き続けます。

## Hook一覧

| Hook | イベント / matcher | 動作 |
|------|--------------------|------|
| [bash_guard](docs/hooks/bash_guard.md) | PreToolUse / `Bash` | 回復不能系を **deny**(`rm -rf /`、`sudo rm`、保護ブランチへの force push — `+refspec` 形式を含む、`mkfs`、`dd`、fork bomb、`DROP TABLE`、`/`・`~` に対する `find … -delete`)。グレー系は **ask**(`git reset --hard`、再帰/強制の `rm`、`curl\|bash`、`curl`/`wget` でのシークレット送信)。保護ブランチは設定可能。連結コマンド(`&&` `;` `\|\|`)は分解して各セグメントを検査。 |
| [secrets_guard](docs/hooks/secrets_guard.md) | PreToolUse / `Read\|Edit\|Write\|Bash` | 機密ファイルの読取・編集・`cat` を deny(`.env` — `.env.example` は許可 — `*.pem`、`id_rsa`、`~/.ssh/`、`~/.aws/credentials`)。さらに**Hook自身の設定・スクリプトを書込保護**(`.claude-hooks.json`、`.claude/settings.json`、`.mcp.json`、`.claude.json`、導入済みの `hooks/`・`rules/`)し、エージェントが自分のガードを無力化できないようにする — シェル変異に加え `curl`/`wget` の出力フラグも検査。読取は許可。 |
| [exfil_guard](docs/hooks/exfil_guard.md) | PreToolUse / `mcp__.*\|WebFetch\|WebSearch` | 外部送信引数のDLP検査(認証情報・PII・機密マーカー・カスタムパターン・任意のsemantic判定)。 |
| [exfil_output_scan](docs/hooks/exfil_output_scan.md) | PostToolUse / `mcp__.*\|WebFetch\|WebSearch` | ツール応答に含まれるシークレット・PIIを検出。警告かマスキングかを設定で選択。 |
| [quality_gate](docs/hooks/quality_gate.md) | PostToolUse / `Edit\|Write` | 編集ファイルへ lint/format を実行し、失敗時は block してClaudeに自己修正させる(warn/block設定可)。 |
| [secrets_scan](docs/hooks/secrets_scan.md) | PostToolUse / `Edit\|Write` | 書き込み内容からAWSキー・GitHubトークン・秘密鍵ブロック等を検出し block。 |
| [audit_log](docs/hooks/audit_log.md) | PreToolUse / PostToolUse / SessionStart / SessionEnd / Stop / ConfigChange / `*` | 全ツール実行とセッション境界を JSONL で非同期記録。 |
| [config_guard](docs/hooks/config_guard.md) | ConfigChange | 検知専用: セッション中の設定変更(および `disableAllHooks` の有効化)を `systemMessage` で可視化。書込保護が見えない経路でガードが黙って無効化されることを防ぐ。 |
| [notify](docs/hooks/notify.md) | Notification | 許可待ち・アイドル時に通知。既定はデスクトップ通知の自動判別(不可ならベル)。bell固定・カスタムコマンドも可。 |

## カスタマイズ

すべての設定は**任意**です — 設定ファイルが無くても全ガードは安全側の既定値で動きます。設定は「有効化」ではなく「調整」のためのものです。deny層を設定ファイルから緩めることはできません。

チームで共有する設定はリポジトリ直下の `.claude-hooks.json` に、個人の既定値は `~/.claude/claude-hooks.json` に置きます。(これらはHook独自の設定ファイルで、Claude Code 本体の `settings.json`(Hookの配線のみを行い、挙動の調整はしない)とは別物です。)最小例:

```json
{
  "bash_guard": {
    "extra_deny": ["docker system prune"],
    "protected_branches": ["main", "release"]
  },
  "exfil_guard": {
    "trusted_servers": ["mcp__internal-kb"]
  }
}
```

全スキーマ・3層マージ・個人用/チーム用/高セキュリティのプリセットは [docs/configuration.md](docs/configuration.md) を参照してください。

## 保証すること / しないこと

**保証すること。** deny層の照合(`bash_guard` / `secrets_guard`)は Claude Code の permission mode に依らず決定論的で、**設定ファイルからは解除できません** — `enabled: false` でも不可で、これは緩いask層のみを無効化します。deny層を外す唯一の方法はHook自体の除去です。

**保証しないこと。** Hooks は Claude Code の `disableAllHooks` や設定削除で丸ごと迂回できます — これは悪意あるユーザー対策ではなく、エージェントの事故防止です。正規表現ルールは未知・難読化された攻撃をすべては捕捉できず、任意の semantic 判定は確率的(`ask` 専用)です。保証すること/しないことの全体像と具体的な既知の限界は[セキュリティモデル](docs/security-model.md)を参照してください。

## License / Contributing

- ライセンス: [LICENSE](LICENSE)(MIT)
- コントリビュート: [CONTRIBUTING.md](CONTRIBUTING.md)
- 変更履歴: [CHANGELOG.md](CHANGELOG.md)
