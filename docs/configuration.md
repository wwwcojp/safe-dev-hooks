# 設定リファレンス(`.claude-hooks.json`)

## 1. 3層マージ

設定は次の3層を、下から上へ(ビルトイン既定 → グローバル → プロジェクト)マージして決定する。上位ほど優先度が高い。

| 優先度 | ファイル | 用途 |
|---|---|---|
| 1(最優先) | プロジェクト直下の `.claude-hooks.json` | チームで共有する設定(コミット対象) |
| 2 | `~/.claude/claude-hooks.json` | 利用者ごとの個人既定値 |
| 3(最下位) | 同梱の `hooks/lib/config.py` 内 `DEFAULTS` | ビルトインの安全側既定値 |

設定ファイルが1つも無くても、全ガードはビルトイン既定値で動作する(「設定は有効化ではなく調整のため」という設計原則)。

### プロジェクト直下とは「プロジェクトルート」のこと(0.7.1)

表の「プロジェクト直下」は、Hookが発火した際のイベントの `cwd`(Claude が Bash の `cd` で移動しうる一時的な作業ディレクトリ)ではなく、次の順序で決定する**プロジェクトルート**(`hooks/lib/config.py` の `project_root(cwd)`)を指す。

```
CLAUDE_PROJECT_DIR(環境変数。下記の検証を通った場合のみ採用)
  → cwd から見た最近傍の祖先で `.git` が存在するディレクトリ(git worktreeでは `.git` はファイルだが同様に扱う)
    → cwd(従来どおりのフォールバック)
```

`CLAUDE_PROJECT_DIR` は次の4条件をすべて満たす場合にだけ採用し、満たさなければ**未設定と同じように**次の段(git 探索 → `cwd`)へフォールバックする(0.7.1、条件2は0.7.2で追加)。

1. **空文字でない** — `CLAUDE_PROJECT_DIR=""` は未設定と同じ扱い
2. **絶対パスである** — 相対パス(`.`・`pkg`・`..` など)はフックプロセスの作業ディレクトリ基準で解決されてしまうため、実在確認より前に弾く
3. **実在するディレクトリである** — 存在しないパス、通常ファイルは採用しない
4. **`cwd` 自身、または `cwd` の祖先である** — シンボリックリンクを解決した実パスで判定する。ただし event に `cwd` が無い呼び出し(`cwd` が `None`)では比較対象が無いためこの条件は評価されず、条件1〜3を満たせば採用される

条件4の理由は §「環境変数由来のアンカー」を参照。`/add-dir` などでセッション開始ディレクトリの外を作業している場合、あるいはClaudeがプロジェクト外へ `cd` した場合(`cd /tmp`・`cd ~` など)は条件4で不採用になり、「実際に作業しているディレクトリの git ルート」が基準になる。エラーではないが、**不採用にした場所に `.claude-hooks.json` があれば、その層が落ちたことを通知する**(下記の通知条件2)。無言では落とさない。

`.claude-hooks.json` は**このプロジェクトルート直下のものだけ**が探索・マージ対象になる。作業ディレクトリ(`cwd`)がサブディレクトリであっても、ルート直下の `.claude-hooks.json` が読まれる点は変わらない——**作業ディレクトリによってプロジェクト層の適用が変わることはない**。

**ただし例外**: サブディレクトリ自身が別の git リポジトリである場合(vendored clone・git submodule・`node_modules` 配下のリポジトリ・その中に作った worktree など)、`.git` の探索はその**ネストしたリポジトリで止まる**ため、そこがプロジェクトルートとして解決される。この状態で発火したHookは親プロジェクトの `.claude-hooks.json` を読まない(0.7.1 でも同じ)。これは無言では起きず、下記の通知が出る。

一方、プロジェクトルート以外の場所に `.claude-hooks.json` が置かれている場合(モノレポのサブパッケージ設定、上記のネストしたリポジトリから見た親プロジェクトの設定など)は、プロジェクトルートと異なる場所にあるため**読まれない**。従来はそこへ `cd` していれば拾われてしまっていたが、0.7.1 からは無言で無視せず `systemMessage` で通知する。通知の条件は次の2つで、いずれも「見つけたのに読まなかった `.claude-hooks.json`」を指す。

1. **`cwd` およびその祖先のうち、基準ディレクトリ以外で `.claude-hooks.json` を持つ場所** — `cwd` 直下に設定ファイルが無くても(サブディレクトリで作業していても)落ちた設定は通知される。文面は「基準ディレクトリと異なる場所にあるため読まなかった」で、対処は内容をプロジェクトルートの `.claude-hooks.json` へ統合すること。
2. **検証で不採用にした `CLAUDE_PROJECT_DIR` の場所に `.claude-hooks.json` があるとき** — 条件4(祖先制約)で不採用にした場所は `cwd` の祖先ではない**別の枝**にあるため、条件1の祖先の探索では拾えない。プロジェクト外へ `cd` した状態はこれに当たり、本来のプロジェクト層が丸ごと落ちる。文面は「環境変数が指しているが `cwd` の祖先でないため基準として採用しなかった」で、対処はそのプロジェクト配下のディレクトリで作業すること(採用条件は緩めない — 可視化のみ)。**ただし値が相対パス(条件2で不採用)の場合はこの通知の対象外** — 相対値をどこかの場所として解決すること自体がフックプロセスの作業ディレクトリに依存してしまい、`cwd` から見て素性の分からない場所を「見つけた」と偽って通知することになるため。

通知は場所ごとに独立したクールダウン(既定 1 時間、`notice_cooldown_sec` で調整。文面・状態管理は §1 信頼層の未承認通知と同じ `hooks/lib/trust.py` に集約)を持つ。

`.gitleaks.toml` の自動探索(`scanners.gitleaks_config` 未指定時)、`config_guard` が `disableAllHooks` を検知する `.claude/settings.json`、`quality_gate` の自動検出(マーカーファイルと実行ディレクトリ)、`audit_log.path` の相対パスも同じ `project_root(cwd)` を基準にする(該当各Hookのリファレンス参照)。**例外**: `secrets_guard` の書込保護対象パスの正規化(「利用者がどこに書こうとしているか」の判定)は、この基準とは別問題として引き続き `cwd` 基準のままである([docs/security-model.md](security-model.md) 項目11)。

git リポジトリでない場所で `CLAUDE_PROJECT_DIR` も未設定の場合は、フォールバックとして従来どおり `cwd` 基準になる(既知の限界)。

#### 環境変数由来のアンカー

環境変数 `CLAUDE_PROJECT_DIR` はハーネス(Claude Code)がフック実行時に注入する値であり、リポジトリ同梱の `.claude/settings.json` の `env` で影響を受け得る。基準ディレクトリが変わっても `trust.gate` は解決後パスの `realpath` をキーに承認を要求するため自己承認はできない。しかし無検証だと、敵対的リポジトリが基準を任意の場所へずらして (a) 本来読まれるべきプロジェクト層を落とす、(b) **利用者が別途承認済みの他プロジェクトの緩和設定**(`secrets_guard.allow_paths` は deny より先に評価される。`bash_guard.allow` は ask/exfil-ask を抑止する)を持ち込む、という2つの操作ができてしまう。

0.7.1 ではこれを2段で塞いでいる。

- **祖先制約**(上記の採用条件4)— 値が `cwd` の祖先でなければ採用しない。ハーネスが注入する正規の値はセッション開始ディレクトリであり、Claude が `cd` した先の `cwd` はその配下にあるのが通常なので、正当な用途は壊れない。無関係なディレクトリ(別プロジェクト)を指す値はここで弾かれるため、(b) の「別プロジェクトの承認済み設定の持ち込み」は成立しない。
- **落ちた層の通知**(上記の通知条件1・2)— 祖先制約を満たす値でも、本来のプロジェクトルートより上位を指せば (a) は依然として可能である。この場合に落ちるのは `cwd` の祖先にある設定なので、通知条件を祖先まで広げてある(条件1)。また、祖先制約で**不採用にした**場所の設定も、そこに `.claude-hooks.json` があれば通知する(条件2)。採用はしないので祖先制約は一切緩まない。**どちらの経路でも、無言で保護が外れることはない。**

### 信頼層: グローバル=信頼 / プロジェクト=要承認(0.7.0)

プロジェクト直下(プロジェクトルート直下)の `.claude-hooks.json` はリポジトリ由来の**信頼できない入力**である(`git clone` しただけで届く)。0.7.0 から、この層は**グローバル設定 `$HOME/.claude/claude-hooks.json` の `trusted_projects` で承認されたプロジェクトに限り**マージされる。未承認なら JSON として解析すらされず、`systemMessage` に承認用エントリが印字される(既定 1 時間のクールダウン付き)。プロジェクト層は生バイト列で読んでから承認判定を行い(`hooks/lib/config.py` の `_read_layer`/`_apply_layer`)、承認された場合のみ同じバイト列をパースする(再オープンしない)。

```jsonc
{
  "trusted_projects": {
    // 既定: 内容ピン留め承認。値は .claude-hooks.json の生バイト列の SHA-256("sha256:" 接頭辞)
    "/home/USER/work/myrepo": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    // ピン留めなし承認(オプトイン): 内容が変わっても採用し続ける。変化した回に 1 度だけ通知
    "/home/USER/work/my-active-repo": true,
    // 明示的な不承認: 採用せず、未承認通知も出さない
    "/home/USER/work/someones-repo": false
  },
  "notice_cooldown_sec": 3600
}
```

- キーはプロジェクトディレクトリの `realpath`(シンボリックリンク解決済み)。worktree やモノレポのサブディレクトリは別パス=別承認
- 承認エントリは**フックが計算して `systemMessage` に印字する**ものを貼り付ける(`sha256sum` を手で叩く必要はない)。承認後に 1 バイトでも変われば「変更検知」の警告が出て採用されない(再承認まで)
- `true`(ピン留めなし)は、**将来そのファイルに書かれる内容に対しても前もって全権限を与える**設定である。信頼の対象がファイル内容でなく「リポジトリのメンテナ」になる、と理解して選ぶこと。変化は検出され 1 度通知される(状態ファイル `$HOME/.claude/safe-dev-hooks-state.json` に依存)
- 状態ファイルは通知の流量制御(クールダウン・変化検知)にのみ使う。読み書きできない場合(`$HOME` が読取専用、パスがディレクトリ等)は**可視性を優先して毎回通知する**——黙って承認状態を進めるより、うるさい方に倒す。承認・不承認の判定そのものは状態ファイルに依存しない。このファイルは書込保護の対象(先回りして書き換えて通知を黙らせる経路を塞ぐ)
- 承認済みプロジェクトの設定は従来どおり**最高優先度の全権限**を持つ(deny 判定の緩和・`quality_gate.commands`/`notify.command` の実行・`scanners.*` のイメージと bind-mount)。鍵ごとの部分承認は無い
- `trusted_projects` はグローバル層からのみ読む(プロジェクト層に書いても自己承認にならない)。値が dict でなければ `_errors` に記録され全プロジェクトが非承認になる
- 通知: 未承認はクールダウン(`notice_cooldown_sec`、`0` で毎回)、ハッシュ不一致は常に、ピン留めなしは変化した回のみ、`false` は出さない。`audit_log` は通知を出さない — **かつクールダウンの枠も消費しない**(`load_config(..., notices=False)`)。`audit_log` は SessionStart と全 PreToolUse/PostToolUse で走る最頻フックであり、表示しないのに枠だけ消費すると以後1時間、対話フック側の通知がまるごと抑止されてしまうため(0.7.1 で修正)
- 承認判定自体(`hooks/lib/trust.py` の `gate()`)は内部で例外を出さない設計だが、万一の異常時も安全側(不採用)に倒し、`systemMessage` に `safe-dev-hooks: プロジェクト設定の信頼判定に失敗したため無視しました: <例外の型>: <メッセージ>` を印字する([docs/security-model.md](security-model.md) §5 fail-open/fail-close方針)
- 承認は `.claude-hooks.json` の採用可否だけでなく、`quality_gate` の**自動検出**
  (`ruff`/`rustfmt`/`npx eslint`)を実行してよいかの判断にも使う(0.8.0)。未承認の
  プロジェクトでは自動検出を実行せず、通知を出す(詳細: [docs/hooks/quality_gate.md](hooks/quality_gate.md))

### マージの規則

- キーごとの再帰的ディープマージ(`hooks/lib/config.py` の `_merge`)。
- オブジェクト(`{...}`)は再帰的にマージされる。
- **配列・文字列・真偽値は、上位の層の値で丸ごと置き換わる(配列は追記ではなく置換)。** 例: プロジェクト設定で `bash_guard.extra_deny` を指定すると、グローバル設定の同キーは使われず置き換わる。

### どの層に何を置くか

- **チーム共有**の設定 → コミット対象のプロジェクト `.claude-hooks.json`。
- **マシン固有の値**(例: `notify.command` の絶対パス)や**個人の既定値** → グローバルの `~/.claude/claude-hooks.json`。
- Claude Code 本体の `settings.json` / `settings.local.json` は本プラグインの設定読み込み対象ではない(混同しやすいので注意)。

### 設定エラー時の挙動(常に安全側)

不正な設定は無視され、該当箇所だけ**直下の層の値**へフォールバックしたうえで `systemMessage` で警告する(検査自体は止めない)。

**縮退先は「直下の層」であって最下層ではない。** 層構造の意味は「上位が下位を上書きする」であり、上位層が壊れた値を持ち込んだときは、その層をマージする前の状態を保つ。検証は層ごとにマージ直後へ挟まれる。

| 不正値のある層 | 縮退先 |
|---|---|
| プロジェクト(`.claude-hooks.json`) | グローバル設定を適用した後の状態 |
| グローバル(`~/.claude/claude-hooks.json`) | ビルトイン既定値 |

したがって、プロジェクト設定が `{"exfil_guard": 0}` のような型のすり替えを行っても、**利用者がグローバル設定で行った強化(`mode: "always"`、`categories.pii: "deny"` 等)は失われない**。最下層へ戻す実装では、プロジェクト設定から中間層の強化を消せてしまう。

- **型不一致**(セクションの型が既定と違う)→ そのセクションを直下の層へ。
- **JSON構文エラー / オブジェクトでない設定ファイル** → そのファイルを無視。
- **読み込み不能な設定ファイル**(不正なUTF-8バイト列、再帰上限を超える深いネスト、読取エラー)→ そのファイルを無視。
- **列挙値のタイポ** → 該当キーのみ直下の層へ。対象は `exfil_guard.mode`・`exfil_output_scan.action`・`quality_gate.mode`・`scanners.gitleaks`・`notify.method`、および `exfil_guard.categories` の各値(`deny`/`ask`/`off`)。直下の層にも存在しない未知のカテゴリキーは削除する。
- **`exfil_guard.categories` がオブジェクトでない** → `categories` 全体を直下の層へ。
- **文字列リストでない値**(`bash_guard.protected_branches`・`secrets_guard.write_protected_paths`)、**`scanners.gitleaks_image` / `gitleaks_config` の型不正** → 該当キーのみ直下の層へ。

いずれも `_errors` に1件ずつ記録され、Hook出力に `[safe-dev-hooks] 設定ファイルに問題があるため既定値で継続: ...` が付く。設定の読み込み(`load_config`)は例外を送出しない設計で、どんな設定ファイルでもHookが判定前に異常終了することはない(deny層が設定ファイル起因で素通りしない)。

## 2. 全スキーマ

以下は実装(`hooks/lib/config.py` の `DEFAULTS`)と一致する全キー・既定値である。

```jsonc
{
  "bash_guard": {
    "enabled": true,
    "extra_deny": [],                        // 追加のdeny正規表現(解除不可)
    "extra_ask": [],                         // 追加のask正規表現
    "allow": [],                             // ask層のみ解除可能な正規表現(deny層は解除不可)
    "protected_branches": ["main", "master", "develop", "release", "production"]
                                              // force-push denyの対象ブランチ(refspec送信先も判定)
                                              // 空リスト [] にすると force-push の deny 昇格を無効化(ask層では拾われ得る)
  },
  "secrets_guard": {
    "enabled": true,
    "protected_paths": [],                   // 追加で保護するファイル名/パスのglobパターン
    "allow_paths": [],                       // 追加で許可するファイル名/パスのglobパターン
    "write_protected_paths": []              // 追加で書込保護するファイル名/パスのglobパターン(ビルトインへマージ、解除不可)
  },
  "exfil_guard": {
    "enabled": true,
    "mode": "detect",                        // "detect"=検知時のみask / "always"=一律ask
    "categories": {
      "credentials": "deny",                 // deny | ask | off
      "pii": "ask",                          // deny | ask | off
      "confidential_markers": "ask",         // deny | ask | off
      "custom": "ask",                       // deny | ask | off
      "semantic": "ask"                      // ask | off(semanticはdeny不可)
    },
    "semantic": {
      "model": "haiku"                       // ヘッドレス判定(claude -p --model)に使うモデル
    },
    "custom_patterns": [],                   // [{ "name": "...", "regex": "..." }, ...]
    "trusted_servers": []                    // ["mcp__internal-kb", ...] 検査スキップ対象
  },
  // 注: semantic判定はペイロード長に関わらず必ず実行される(D16)ため、
  // MCP/WebFetch/WebSearchの呼び出しごとにヘッドレスClaude実行のレイテンシ(数秒程度)と
  // トークンコストが発生する。重い場合は categories.semantic を "off" にするか、
  // 信頼できるサーバーを trusted_servers に登録して検査自体をスキップする。
  "exfil_output_scan": {
    "enabled": true,
    "action": "warn"                         // "warn" | "redact"(redactは文字列応答のみ有効)
  },
  "quality_gate": {
    "enabled": true,
    "mode": "block",                         // "block"=Claudeに修正させる / "warn"=注記のみ
    "commands": {}                           // { "<ファイル名glob>": ["ruff check {file}", ...] }
  },
  "secrets_scan": {
    "enabled": true,
    "custom_patterns": []                    // [{ "name": "...", "regex": "..." }, ...] ビルトインへマージ
  },
  "audit_log": {
    "enabled": true,
    "path": ".claude/logs"                   // 相対パスはプロジェクトルート起点(0.7.1。絶対パス指定時はそのまま)
  },
  "config_guard": {
    "enabled": true                          // セッション中の設定変更(ConfigChange)を通知。警告専用のためfalseで無効化可
  },
  "notify": {
    "enabled": true,
    "method": "auto",                        // "auto"=デスクトップ通知の自動判別(不可ならベル) / "bell"=常にベル
    "command": null                          // 設定時はmethodより優先。{message} 置換で実行
  },
  "scanners": {
    "gitleaks": "auto",                      // "auto"=PATH上に`gitleaks`があれば内蔵patternsに加算(無ければ無コストでスキップ)
                                              // "off"=内蔵patternsのみ / "docker"=`docker run`経由で明示opt-in
    "gitleaks_image": "ghcr.io/gitleaks/gitleaks:v8.30.1",
                                              // "docker"モードで使うイメージ(既定は固定タグ)
    "gitleaks_config": null                  // gitleaksの`-c`に渡す.gitleaks.tomlパス。未指定時は<プロジェクトルート>/.gitleaks.tomlが
                                              // 存在すれば自動採用(0.7.1。基準は上記「基準ディレクトリの決定」参照)、無ければgitleaks既定設定で実行する
  },
  "trusted_projects": {},                    // プロジェクト設定の承認記録(グローバル層専用)。
                                              // { "<realpath>": "sha256:<hex64>" | true | false }。§1 信頼層を参照
  "notice_cooldown_sec": 3600                // 未承認通知のクールダウン秒。0 で毎回通知
  // 注: scanners.gitleaksは secrets_scan/exfil_guard/exfil_output_scan が共有する秘密検出バックエンドの設定である
  // (`hooks/lib/scanners.py` の scan_secrets)。内蔵patterns(rules/secret_patterns.json)は常に無条件で走るfloorであり、
  // gitleaksの検出結果はその上にunion加算されるだけで置き換えない(不在・失敗時もfloorは不変=credentials=denyの保証は
  // 弱まらない)。gitleaks呼び出し自体はタイムアウト(15秒)・プロセス起動失敗・非0/1終了・JSON解析失敗のいずれでも
  // 例外を出さずfail-open(その回の加算だけ無し、floor自体は継続)する。
  // "docker"モードは`docker run`の起動コストが掛かるため、secrets_scan(10秒)・exfil_output_scan(15秒)の短い
  // timeoutでは予算超過し得る。有効化するなら60秒timeoutを持つexfil_guardでの利用を基本にする。また
  // `DOCKER_HOST`がリモートdaemonを指す環境では、`docker run`実行時に検査対象ペイロード(stdin経由)がその
  // リモートホストへ送信され得る点に注意(既定はローカルdaemon前提)。
}
```

各Hookの設定キーの詳細は個別のHookリファレンスも参照してください: [bash_guard](hooks/bash_guard.md) / [secrets_guard](hooks/secrets_guard.md) / [exfil_guard](hooks/exfil_guard.md) / [exfil_output_scan](hooks/exfil_output_scan.md) / [quality_gate](hooks/quality_gate.md) / [secrets_scan](hooks/secrets_scan.md) / [audit_log](hooks/audit_log.md) / [config_guard](hooks/config_guard.md) / [notify](hooks/notify.md)。

`scanners.*` はどのHook個別のセクションにも属さない共有設定であり、`secrets_scan`/`exfil_guard`(`categories.credentials`)/`exfil_output_scan` の3Hookが `scan_secrets()` 経由で共通して参照する。

## 3. 設計原則

- **安全側の既定**: 設定ファイルが無くても全ガードが既定値で動く。
- **denyの解除は不可**: `bash_guard.allow` で解除できるのは ask 層のみ。回復不能系 deny は設定ファイルからは一切解除できない。`enabled: false` は deny 層を解除しない — `bash_guard.enabled: false` は ask 層(`bash_ask.json`・`extra_ask`・curl/wgetの外部送信ask検査)のみを無効化し、`secrets_guard.enabled: false` はdeny層に対してはno-op(`systemMessage` で通知のうえ検査を継続)である。deny層を止める唯一の正規手段は `hooks/hooks.json` からのHook除去、または Claude Code 本体の `disableAllHooks` である。
- **データ駆動**: 危険パターン・シークレット形式・PII形式は `rules/*.json` に集約されており、コード変更なしで拡張可能。`extra_deny`/`extra_ask`/`custom_patterns`/`protected_paths`/`allow_paths` はビルトインへマージされる。
- **quality_gateの自動検出**: `commands` 未指定時は拡張子と `pyproject.toml`(ruff)、`package.json`(eslint)、`Cargo.toml`(rustfmt)等から推定する。検出不能なら何もしない。

## 4. 設定プリセット例

### 4.1 個人用(既定のまま)

個人利用では設定ファイルを作らない、または空の `{}` で十分。全ガードがビルトイン既定値(安全側)で動作する。

```json
{}
```

### 4.2 チーム用(`custom_patterns` + `trusted_servers` 追加)

> 0.7.0 以降、このプリセットをプロジェクトの `.claude-hooks.json` に置いた場合、**メンバー各自が一度承認**する必要がある(§1 信頼層)。`trusted_servers` のようにガードを緩める値は、そもそもプロジェクト層で共有せず各自のグローバル設定に置くことを勧める。

社内ドメインへの言及を検出し、社内ナレッジベースMCPサーバーは検査から除外する例。

```json
{
  "bash_guard": {
    "extra_deny": ["docker system prune -a"],
    "protected_branches": ["main", "master", "develop", "release", "production"]
  },
  "exfil_guard": {
    "custom_patterns": [
      { "name": "社内ドメイン", "regex": "[\\w.-]+\\.example\\.co\\.jp" },
      { "name": "顧客ID", "regex": "\\bCUST-\\d{6}\\b" }
    ],
    "trusted_servers": ["mcp__internal-kb", "mcp__internal-docs"]
  },
  "quality_gate": {
    "commands": {
      "*.py": ["ruff check {file}", "ruff format --check {file}"]
    }
  }
}
```

### 4.3 高セキュリティ(`exfil_guard.mode=always`, `exfil_output_scan.action=redact`)

すべての外部送信を一律askにし、応答からのシークレット・PII漏洩はマスキングして遮断する例。

```json
{
  "exfil_guard": {
    "mode": "always",
    "categories": {
      "credentials": "deny",
      "pii": "deny",
      "confidential_markers": "ask",
      "custom": "ask",
      "semantic": "ask"
    },
    "trusted_servers": []
  },
  "exfil_output_scan": {
    "action": "redact"
  },
  "secrets_guard": {
    "protected_paths": ["config/secrets/**", "**/*.credentials"],
    "write_protected_paths": ["deploy/*.lock", "infra/**/*.tfstate"]
  },
  "scanners": {
    "gitleaks": "docker"
  }
}
```

`exfil_output_scan.action: "redact"` は `tool_output`/`tool_response` が文字列型の応答にのみ有効([docs/hooks/exfil_output_scan.md](hooks/exfil_output_scan.md) の既知の限界を参照)。

`scanners.gitleaks: "docker"` は内蔵patternsの検出漏れを補う任意のunion加算であり、明示opt-inのため既定は `"auto"` のままでよい。ローカルに `gitleaks` バイナリを常設できない環境で、`exfil_guard`(timeout 60秒)経由の検出強化だけを狙う場合に有効。`docker`モードの前提・タイムアウト上の注意は[2. 全スキーマ](#2-全スキーマ)の `scanners` の項を参照。
