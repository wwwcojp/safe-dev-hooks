# config_guard

## 目的

セッション中に設定ファイル(ユーザー/プロジェクトの `settings.json`、`settings.local.json`、managed policy、skills)が変更されたことをユーザーへ通知する**検知専用**のフック。[secrets_guard](secrets_guard.md) の write_protected(予防層)を素通りする経路 — インタプリタレベルの書込、Claude Code の外で動く別プロセス、人間の手による編集 — で設定が変わっても、変更の発生自体を必ず可視化する。

## 対象イベント / matcher

- イベント: `ConfigChange`(設定ファイルの変更時に発火)
- matcher: なし(全ての変更元 — `user_settings` / `project_settings` / `local_settings` / `policy_settings` / `skills` — を対象)
- timeout: 10秒(`hooks/hooks.json`)
- 同イベントで `audit_log` も配線されており、変更はJSONL監査ログにも記録される

> **注**: `ConfigChange` は比較的新しいイベントのため、古いClaude Codeでは発火しない。その場合フックは単に呼ばれないだけで、他のフックの動作には影響しない。

## 動作

1. 変更元(イベント入力の `source` 等。フィールド名は公式に未文書化のため複数候補を防御的に読む)を含む `systemMessage` で「設定が変更された」ことを通知する。
2. 変更後のユーザー/プロジェクト設定に `disableAllHooks: true` が含まれる場合、「全Hooks(本ガードを含む)が無効化される」旨の警告を追加する。

## ブロックしない理由(設計判断)

`ConfigChange` はexit 2やJSON出力で変更の適用自体をブロックできるが、config_guard は意図的に**警告のみ**とする:

- `disableAllHooks` は本Hooks集が公式に認める唯一のHooks完全無効化手段([security-model](../security-model.md) §2)であり、これをブロックすると正規の解除経路と矛盾する。
- `ConfigChange` は人間自身がエディタで行った設定変更でも発火し、フックからは変更者を区別できない。ブロックすると正当な設定作業を止めてしまう(過剰検知は安全側ではない — `.claude/rules/guard-rule-changes.md` 原則2)。
- 新しい検出はまず警告として観測し、誤検知の実態を確認してから強化する(warn→block の段階導入。[best-practices](../best-practices.md) §6.3)。

つまり config_guard は「予防(write_protected)を抜けた変更を、事後すぐに人間へ知らせる」検知層であり、deny層ではない。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `config_guard.enabled` | `true` | `false` で無効化できる。警告専用(deny層ではない)ため、他のガードと異なり enabled:false が完全に有効 |

## フェイルモード

fail-open。フック自体の異常時はツール実行・設定変更を妨げず、`systemMessage` で検査スキップを通知する。
