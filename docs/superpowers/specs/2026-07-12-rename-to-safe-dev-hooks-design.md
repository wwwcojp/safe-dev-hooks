# リネーム統一設計: safe-dev-hooks への名称移行

- 日付: 2026-07-12
- ステータス: 承認済み

## 背景

ローカルディレクトリが `~/claude-code-hooks` から `~/safe-dev-hook` にリネームされたが、GitHubリポジトリ名(`wwwcojp/claude-code-hooks`)、リポジトリ内の文書・設定、および実利用環境(`~/.claude/settings.json`)には旧名参照が残っている。さらにディレクトリ名(単数 `safe-dev-hook`)とプラグイン名・pyproject名(複数 `safe-dev-hooks`)の揺れがある。

## 決定事項

1. 正式名称は **`safe-dev-hooks`**(複数形)に統一する。既存のプラグイン名・pyproject名と一致させる。
2. GitHubリポジトリも `wwwcojp/safe-dev-hooks` にリネームする(旧URLは自動リダイレクトされる)。
3. ローカルディレクトリも `~/safe-dev-hooks` に再リネームする。
4. 日付付きの歴史的文書(`docs/superpowers/specs/`・`plans/` の既存ファイル)は記録として旧名のまま残す。旧URLはGitHubリダイレクトで辿れるため実害はない。運用文書(handoff、best-practices)は新名に更新する。

## 変更対象

### リポジトリ内(コミット対象)

| ファイル | 変更内容 |
|---|---|
| `README.md` / `README.ja.md` | タイトル、クローンURL、`/plugin marketplace add wwwcojp/safe-dev-hooks`、パス例 `$HOME/safe-dev-hooks` |
| `examples/settings.full.json` / `settings.minimal.json` | `$HOME/claude-code-hooks/...` → `$HOME/safe-dev-hooks/...`(計14箇所) |
| `.claude-plugin/marketplace.json` | `"name": "claude-code-hooks"` → `"safe-dev-hooks"` |
| `docs/superpowers/2026-07-05-handoff.md` | 運用文書のため旧名参照(4箇所)を新名に更新 |

`docs/best-practices.md` 内の `claude-code-hooks` を含む文字列はすべて外部リポジトリ名(disler/claude-code-hooks-mastery 等)のため変更しない。

日付付きspec/plan(`2026-07-03-safe-dev-hooks-design.md`、`2026-07-03-safe-dev-hooks.md`)は変更しない。

### リポジトリ外(環境操作)

1. `gh repo rename safe-dev-hooks` — GitHubリポジトリ名変更。origin remoteも自動更新される。
2. プラグイン再登録 — `~/.claude/settings.json` に加えて `~/.claude/plugins/known_marketplaces.json`・`installed_plugins.json`・キャッシュにも旧名参照があるため、手編集ではなく公式コマンドで再登録する:
   - `claude plugin uninstall safe-dev-hooks@claude-code-hooks`
   - `claude plugin marketplace remove claude-code-hooks`
   - `mv ~/safe-dev-hook ~/safe-dev-hooks`(ディレクトリリネーム。再登録前に行う)
   - `claude plugin marketplace add $HOME/safe-dev-hooks`
   - `claude plugin install safe-dev-hooks@safe-dev-hooks`

## 実施順序

1. リポジトリ内の参照更新をコミット
2. `gh repo rename` でGitHub側をリネームし、push
3. 旧プラグイン登録の削除 → ローカルディレクトリ再リネーム → 新パスでプラグイン再登録
4. 新パスでセッションを開き直し、全Hookの発火スモークテストで動作確認

## エラー処理・注意点

- 手元に未コミットの変更(`rules/bash_deny.json` の変更、未追跡の `CLAUDE.md`)があるが、本件とは無関係のためコミットに含めない。
- ディレクトリ再リネーム後、旧パスを参照するシェルの作業ディレクトリや開きっぱなしのセッションは無効になる。セッションの開き直しが必要。
- `gh repo rename` 後も旧URLへの `git push` はリダイレクトで成功するが、remoteは新URLに更新しておく(ghが自動で行う)。

## 検証

- `grep -rn "claude-code-hooks"` で残存参照が歴史的文書のみであることを確認
- `git remote -v` が新URLを指すことを確認
- 新パスでClaude Codeを起動し、bash_guard等のHookが発火することを確認
