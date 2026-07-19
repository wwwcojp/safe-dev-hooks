# アーカイブ

実装が完了し、現行の設計・コードでは継続参照する必要がなくなった superpowers 成果物を履歴として保管する。CLAUDE.md の「実装完了時にPlanの保存価値を判断し、保持・アーカイブ・削除のいずれかを行う」に基づくアーカイブ層。

現行の設計は `docs/superpowers/specs/`、進行中/直近の記録は `docs/superpowers/` 直下を参照すること。

## 保管物

### `specs/`(陳腐化した設計)

- `2026-07-12-notify-wrapper-design.md` — `examples/notify_wrapper.sh` の設計。0.3.0 でラッパーは削除され、`specs/2026-07-16-notify-desktop-integration-design.md` に置き換わった。
- `2026-07-12-rename-to-safe-dev-hooks-design.md` — リポジトリ名を `safe-dev-hooks` に統一した一度きりの改名作業の設計。

### `plans/`(実装完了済みの計画)

内容はコード・CHANGELOG・対応する spec に実現済み。実装足場として履歴保管する。

- `2026-07-03-safe-dev-hooks.md` — 初期 8 Hooks(14タスク)の実装計画。恒久記録は `docs/superpowers/2026-07-05-handoff.md`。
- `2026-07-16-notify-desktop-integration.md` — notify デスクトップ通知統合(0.3.0)。
- `2026-07-18-hooks-hardening.md` — ガードのハードニング(0.4.0)。
