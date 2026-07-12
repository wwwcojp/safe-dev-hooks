# Superpowers artifacts

- `docs/superpowers/specs/` はGit管理し、設計レビューの対象とする。
- `docs/superpowers/plans/` は実装ブランチ上でGit管理する。
- Planには認証情報、個人情報、顧客固有情報、本番環境の内部情報を記載しない。実ユーザー名・実ホームパスも個人情報に含む(プレースホルダー規約は `.claude/rules/no-personal-paths.md`)。
- 実装完了時にPlanの保存価値を判断し、保持・アーカイブ・削除のいずれかを行う。
- `docs/superpowers/` は公開ドキュメントのビルド対象から除外する。
- コード変更と矛盾するSpecは、同じPRで更新する。