# Superpowers artifacts

- `docs/superpowers/specs/` はGit管理し、設計レビューの対象とする。
- `docs/superpowers/plans/` は実装ブランチ上でGit管理する。
- Planには認証情報、個人情報、顧客固有情報、本番環境の内部情報を記載しない。実ユーザー名・実ホームパスも個人情報に含む(プレースホルダー規約は `.claude/rules/no-personal-paths.md`)。
- 実装完了時にPlanの保存価値を判断し、保持・アーカイブ・削除のいずれかを行う。アーカイブは削除でなく `docs/superpowers/archive/` へ移動する(完了Plan・陳腐化Spec)。恒久記録は `CHANGELOG.md`・`docs/security-model.md`・`docs/superpowers/2026-07-05-handoff.md` に集約し、`.superpowers/` はephemeralなscratch(gitignore)とする。
- `docs/superpowers/` は公開ドキュメントのビルド対象から除外する。
- コード変更と矛盾するSpecは、同じPRで更新する。

# 規約(`.claude/rules/`)

該当する作業を始める前に読むこと。

- `.claude/rules/no-personal-paths.md` — 実ユーザー名・実ホームパスの禁止(公開リポジトリ)。
- `.claude/rules/guard-rule-changes.md` — `bash_guard`/`secrets_guard`・`rules/*.json` を変更するときの設計原則。
- `.claude/rules/dogfooding.md` — 自リポジトリで作業するとき、自分のガードで止まらないための回避。