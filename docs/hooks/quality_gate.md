# quality_gate

## 目的

`Edit`/`Write` で編集されたファイルに対しlint/formatチェックを実行し、失敗した場合はClaudeへフィードバックして自己修正させる(または警告のみに留める)。

## 対象イベント / matcher

- イベント: `PostToolUse`
- matcher: `Edit|Write`
- timeout: 90秒(`hooks/hooks.json`。個別コマンドの内部タイムアウトは45秒)

編集先ファイルが存在しない場合(削除された等)は何もしない。

## 判定基準

`.claude-hooks.json` の `quality_gate.commands` に、編集ファイル名(`fnmatch` によるファイル名一致。ディレクトリを含むパスパターンでは一致しない)に対応するコマンド配列が定義されていればそれを使用する。未定義の場合は自動検出にフォールバックする。

### 自動検出(`commands` 未指定時)

| 対象拡張子 | 必要な実行ファイル | 前提設定ファイル(いずれか) | 実行コマンド |
|---|---|---|---|
| `*.py` | `ruff` | `pyproject.toml` / `ruff.toml` / `.ruff.toml` | `ruff check {file}` |
| `*.rs` | `rustfmt` | `Cargo.toml` | `rustfmt --check {file}` |
| `*.js` / `*.jsx` / `*.ts` / `*.tsx` | `npx` | `package.json` | `npx --no-install eslint {file}` |

実行ファイルが `PATH` に無い、または前提設定ファイルがプロジェクトルート直下に無い場合はそのコマンドをスキップする。いずれの条件も満たさなければ何もしない。「プロジェクトルート」は `hooks/lib/config.py` の `project_root(cwd)`(`CLAUDE_PROJECT_DIR` → cwdの最近傍のgitルート → cwd)で決定する([docs/configuration.md](../configuration.md) 参照)。

**自動検出は承認済みプロジェクトでのみ実行する(0.8.0)**: `ruff`/`rustfmt`/`npx eslint` はいずれもプロジェクト同梱の設定ファイルを読み込み、`eslint.config.js` に至っては JavaScript として評価される。これをリポジトリの承認前に実行するのは、`.claude-hooks.json` の未承認内容をマージしないという 0.7.0 の原則に反する。そこで自動検出は、グローバル設定 `$HOME/.claude/claude-hooks.json` の `trusted_projects` にそのプロジェクトのエントリがある場合(内容ピン留め `"sha256:…"` またはピン留めなし `true`。`false` と未登録は非承認)に限り実行する。承認の有無は判定していない `.claude-hooks.json` の**有無とは無関係**であり、判定しているのは「利用者がこのディレクトリ(プロジェクトルート)を承認したか」である(設定ファイルが無いプロジェクトでも、承認さえされていれば自動検出は動く)。利用者が `quality_gate.commands` に明示したコマンドはこの制限を受けない — グローバル設定に書いたものはもともと承認不要、プロジェクト設定に書いたものは 0.7.0 のプロジェクト設定ゲート自体で既に承認制になっている。未承認のため自動検出をスキップした場合は `systemMessage` で通知する(既定 1 時間のクールダウン、`notice_cooldown_sec` で調整)。詳細: [docs/security-model.md](../security-model.md) §2。

**実行ディレクトリ(0.7.1)**: `commands` で明示設定したコマンドも自動検出コマンドも、区別なく**プロジェクトルートを実行ディレクトリ**として実行する。`{file}` は Edit/Write が常に絶対パスで渡すため置換結果はこの変更の影響を受けないが、利用者が `commands` に書いた**相対パスを含む独自コマンド**は、従来の `cwd`(作業ディレクトリ)ではなく**プロジェクトルート**基準で解決されるようになった点に注意。

### block になる条件

コマンドが1つ以上実行され、いずれかが非ゼロ終了した場合。`quality_gate.mode` が `"block"`(既定)であれば `decision: "block"` を返し、失敗したコマンドと出力の末尾1500文字をClaudeへ提示して修正を促す。

### warnになる条件

同じ失敗条件で `quality_gate.mode` が `"warn"` の場合、`additionalContext` に警告文を追加するのみでツール実行は継続する。

### 何もしない条件

対象コマンドが無い、または全コマンドが成功した場合。

## 設定キー

| キー | 既定値 | 説明 |
|---|---|---|
| `quality_gate.enabled` | `true` | falseで本Hookを無効化 |
| `quality_gate.mode` | `"block"` | `"block"` = Claudeに修正させる / `"warn"` = 注記のみ |
| `quality_gate.commands` | `{}` | `{"<ファイル名glob>": ["コマンド {file} ..."]}`。`{file}` は `shlex.quote` 済みのファイルパスに置換される。指定するとそのglobに対する自動検出は使われない |

## 既知の限界

- `commands` のキーは `fnmatch` による**ファイル名のみ**の一致であり、ディレクトリを含むパスパターン(例: `"src/*.py"`)は一致しない。
- 自動検出はプロジェクト**ルート直下**の設定ファイル(`pyproject.toml` 等)の有無のみで判定するため、モノレポでサブディレクトリにのみ設定ファイルがある場合は自動検出されない。
- `npx --no-install eslint` はローカルインストール済みのESLintのみを使う(グローバルインストールへのフォールバックはしない)ため、ローカル未導入のプロジェクトでは自動検出コマンドが実行できず失敗として記録される(`エラーコード != 0` として `run_checks` に失敗扱いで積まれる)。
- 1本のコマンドにつき内部タイムアウトは45秒。複数コマンドが直列実行されるため、Hook全体のtimeout(90秒)を超えると強制終了され得る。
- 対象は編集された1ファイルのみで、プロジェクト全体のlintは実行しない。
- Hook自体の異常時は fail-open(検査スキップ、編集は通過)。
