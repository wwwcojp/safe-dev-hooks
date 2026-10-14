#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Bashコマンドの破壊的操作を deny/ask の2段階でガードし、ヒットは LLM で偽陽性判定する。"""
import fnmatch
import json
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from hooks.lib import config, hook_io, llm_judge, patterns  # noqa: E402


def _segments(command: str) -> list[str]:
    parts = re.split(r"(?:&&|\|\||;|\n)", command)
    return [p.strip() for p in parts if p.strip()]


def _normalize(text: str) -> str:
    # クォートによるすり抜け対策(過剰検知側に倒す)
    return text.replace('"', "").replace("'", "")


_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([^\s'\"$`(){}]+)$")


def _expand_simple_assignments(command: str) -> str:
    """同一コマンド内の単純な定数代入(VAR=value)を後続の $VAR/${VAR} に展開する。"""
    assignments: dict[str, str] = {}
    for seg in _segments(command):
        m = _ASSIGN_RE.match(seg)
        if m:
            assignments[m.group(1)] = m.group(2)
    if not assignments:
        return command
    expanded = command
    for name, value in assignments.items():
        pattern = r"\$\{" + re.escape(name) + r"\}"
        expanded = re.sub(pattern, lambda m, v=value: v, expanded)
        pattern = r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])"
        expanded = re.sub(pattern, lambda m, v=value: v, expanded)
    return expanded


_SEND_CMD_RE = re.compile(
    r"\b(curl|wget)\b[^;|&]*?"
    r"(-d\b|--data\b|--data-[a-z]+\b|-F\b|--form\b|-T\b|--upload-file\b"
    r"|--post-data\b|--post-file\b|--body-data\b|--body-file\b)"
)
_ENV_REF_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")
_CMD_SUBST_RE = re.compile(r"\$\(|`")


def _has_sensitive_operand(segment: str) -> bool:
    if _ENV_REF_RE.search(segment) or _CMD_SUBST_RE.search(segment):
        return True
    protected = patterns.load_rules("sensitive_paths.json")["protected"]
    for tok in segment.split():
        name = os.path.basename(tok.strip("\"'").rstrip("/"))
        if any(fnmatch.fnmatch(name, pat) for pat in protected):
            return True
    return False


def _exfil_ask(segment: str) -> dict | None:
    if not _SEND_CMD_RE.search(segment):
        return None
    if _has_sensitive_operand(segment):
        return {
            "decision": "ask",
            "reason": (
                "外部送信コマンドに機微オペランド(環境変数/コマンド置換/機密ファイル)を検出。"
                "送信内容を確認してください"
            ),
        }
    return None


EXFIL_RULE_NAME = "exfil-sensitive-send"
EXFIL_DESCRIPTION = (
    "環境変数・コマンド置換・機密ファイルを伴う curl/wget のデータ送信(機微情報の外部送信)"
)


def _force_push_rules(cfg: dict) -> list[dict]:
    branches = cfg.get("protected_branches")
    if branches is None:  # キー未指定(直接呼び出し等)は最小限の既定で保護
        branches = ["main", "master"]
    if not branches:  # 空リスト = 保護ブランチ無し(force-push規則を生成しない)
        return []
    alt = "|".join(re.escape(b) for b in branches)
    desc = f"保護ブランチ({', '.join(branches)})への強制プッシュ(リモート履歴の上書き)"
    return [
        {"name": "force-push-protected", "description": desc,
         "regex": rf"\bgit\s+push\s+[^;|&]*(--force\b|-f\b)[^;|&]*\b({alt})\b"},
        {"name": "force-push-protected-order", "description": desc,
         "regex": rf"\bgit\s+push\s+[^;|&]*\b({alt})\b[^;|&]*(--force\b|-f\b)"},
        {"name": "force-push-refspec", "description": desc,
         "regex": rf"\bgit\s+push\b[^;|&]*\s\+(?:[^\s:]*:)?(?:[^\s:]*/)?({alt})(?![\w.@:/-])"},
    ]


def _user_rule(kind: str, pattern: str) -> dict:
    return {
        "name": f"extra_{kind}:{pattern}",
        "regex": pattern,
        "description": f"利用者が追加した正規表現 {pattern} に一致する操作",
    }


def _rules(cfg: dict) -> tuple[list[dict], list[dict], list[str]]:
    deny_rules = (
        list(patterns.load_rules("bash_deny.json"))
        + _force_push_rules(cfg)
        + [_user_rule("deny", p) for p in cfg.get("extra_deny", [])]
    )
    ask_rules = list(patterns.load_rules("bash_ask.json")) + [
        _user_rule("ask", p) for p in cfg.get("extra_ask", [])
    ]
    return deny_rules, ask_rules, cfg.get("allow", [])


def _targets(command: str) -> list[str]:
    targets = [_normalize(s) for s in _segments(command)] + [_normalize(command)]
    expanded = _expand_simple_assignments(command)
    if expanded != command:
        targets += [_normalize(s) for s in _segments(expanded)] + [_normalize(expanded)]
    return targets


def _deny_reason(rule: dict) -> str:
    return f"破壊的コマンドを検出: {rule['name']}(deny層は設定で解除できません)"


def _ask_reason(rule: dict) -> str:
    return f"注意が必要なコマンドを検出: {rule['name']}。実行してよいか確認してください"


def evaluate(command: str, cfg: dict) -> dict | None:
    enabled = cfg.get("enabled", True)
    deny_rules, ask_rules, allow = _rules(cfg)
    targets = _targets(command)
    for rule in deny_rules:
        if any(re.search(rule["regex"], t) for t in targets):
            return {"decision": "deny", "reason": _deny_reason(rule)}
    if not enabled:
        return None
    for rule in ask_rules:
        for t in targets:
            if re.search(rule["regex"], t) and not any(re.search(a, t) for a in allow):
                return {"decision": "ask", "reason": _ask_reason(rule)}
    for seg in _segments(command):
        nseg = _normalize(seg)
        verdict = _exfil_ask(nseg)
        if verdict and not any(re.search(a, nseg) for a in allow):
            return verdict
    return None


def collect_hits(command: str, cfg: dict) -> list[dict]:
    """LLM 判定用に、ヒットした全ルールを deny → ask → 外部送信 ask の順で集める。

    evaluate() は先頭のヒットで止まるが、LLM 判定では「1 つの deny が偽陽性でも
    別の deny が本物なら止める」必要があるため全件を見る。先頭要素は常に evaluate()
    の結果と一致する(test_collect_hits_first_hit_matches_evaluate)。
    """
    enabled = cfg.get("enabled", True)
    deny_rules, ask_rules, allow = _rules(cfg)
    targets = _targets(command)
    hits: list[dict] = []

    def add(rule: dict, decision: str, reason: str) -> None:
        if any(h["name"] == rule["name"] for h in hits):
            return
        hits.append({
            "name": rule["name"], "regex": rule["regex"],
            "description": rule.get("description", ""),
            "decision": decision, "reason": reason,
        })

    for rule in deny_rules:
        if any(re.search(rule["regex"], t) for t in targets):
            add(rule, "deny", _deny_reason(rule))
    if not enabled:
        return hits
    for rule in ask_rules:
        if any(
            re.search(rule["regex"], t) and not any(re.search(a, t) for a in allow)
            for t in targets
        ):
            add(rule, "ask", _ask_reason(rule))
    for seg in _segments(command):
        nseg = _normalize(seg)
        verdict = _exfil_ask(nseg)
        if verdict and not any(re.search(a, nseg) for a in allow):
            exfil = {"name": EXFIL_RULE_NAME, "regex": "", "description": EXFIL_DESCRIPTION}
            add(exfil, "ask", verdict["reason"])
            break
    return hits


LLM_TIMEOUT_SEC = 40  # hooks.json の timeout 60 秒に uv の起動分の余裕を残す
LLM_MAX_COMMAND = 20000
LLM_REASON_MAX = 200
_RULES_JSON_KWARGS = {"ensure_ascii": False, "indent": 2}
_PLACEHOLDER_RE = re.compile(r"\{(rules|delimiter|command)\}")


def build_prompt(command: str, hits: list[dict], delimiter: str) -> str:
    """固定テンプレートに埋め込む。置換は1パスで行い、コマンド内の {...} は展開しない。"""
    path = patterns.RULES_DIR / "bash_fp_prompt.md"
    template = path.read_bytes().decode("utf-8")  # ロケールに依らず UTF-8 で読む
    entries = [
        {"rule": h["name"], "description": h["description"], "regex": h["regex"]} for h in hits
    ]
    rules = json.dumps(entries, **_RULES_JSON_KWARGS)
    values = {"rules": rules, "delimiter": delimiter, "command": command}
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def parse_verdicts(data: dict, hits: list[dict]) -> dict[str, tuple[bool, str]]:
    """LLM 出力を厳格に解析する。真偽値の true だけを偽陽性とし、不正は ValueError。"""
    items = data.get("verdicts")
    if not isinstance(items, list):
        raise ValueError("verdicts が配列ではありません")
    names = {h["name"] for h in hits}
    result: dict[str, tuple[bool, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("判定の要素がオブジェクトではありません")
        name, fp = item.get("rule"), item.get("false_positive")
        if not isinstance(name, str) or name not in names:
            raise ValueError(f"ヒットしていないルールの判定があります: {name!r}")
        if not isinstance(fp, bool):
            raise ValueError(f"{name} の false_positive が真偽値ではありません")
        if name in result:
            if result[name][0] != fp:
                raise ValueError(f"{name} に矛盾する判定があります")
            continue
        reason = item.get("reason")
        result[name] = (fp, reason[:LLM_REASON_MAX] if isinstance(reason, str) else "")
    missing = names - result.keys()
    if missing:
        raise ValueError(f"判定が欠けているルールがあります: {', '.join(sorted(missing))}")
    return result


def _describe(hits: list[dict], verdicts: dict[str, tuple[bool, str]]) -> str:
    # 理由は LLM の出力(コマンド経由で攻撃者が影響し得る)なので、未検証と明示する
    details = "; ".join(f"{h['name']}: {verdicts[h['name']][1] or '(理由なし)'}" for h in hits)
    return f"LLMの判断(未検証): {details}"


def combine(
    hits: list[dict], verdicts: dict[str, tuple[bool, str]], mechanical: dict
) -> tuple[dict | None, str | None]:
    """判定表(spec)に従って最終判定を決める。deny は ask より下げない。"""
    deny_hits = [h for h in hits if h["decision"] == "deny"]
    ask_hits = [h for h in hits if h["decision"] == "ask"]
    if deny_hits:
        if not all(verdicts[h["name"]][0] for h in deny_hits):
            return mechanical, None
        reason = (
            f"破壊的コマンドのルールに一致しましたが、LLM判定で字面の誤一致と判断されたため"
            f" deny から ask に降格しました({_describe(deny_hits, verdicts)})。"
        )
        real_asks = [h for h in ask_hits if not verdicts[h["name"]][0]]
        if real_asks:
            names = ", ".join(h["name"] for h in real_asks)
            reason += f"あわせて注意が必要なコマンドを検出: {names}。"
        return {"decision": "ask", "reason": reason + "実行してよいか確認してください"}, None
    real_asks = [h for h in ask_hits if not verdicts[h["name"]][0]]
    if real_asks:
        # LLM の理由文は承認を促す文言を差し込まれ得るので載せず、ルール名だけ示す
        names = ", ".join(h["name"] for h in real_asks)
        reason = f"{mechanical['reason']}(LLM判定でも字面の誤一致ではないと判断されました: {names})"
        return {"decision": "ask", "reason": reason}, None
    return None, (
        "[safe-dev-hooks] bash_guard: LLM判定で字面の誤一致と判断し、確認を省略しました"
        f"({_describe(ask_hits, verdicts)})"
    )


def llm_review(command: str, cfg: dict, mechanical: dict) -> tuple[dict | None, str | None]:
    """機械判定のヒットを LLM で偽陽性判定する。例外は出さず、判定不能なら機械判定を返す。"""
    review_cfg = cfg.get("llm_review") or {}
    if not review_cfg.get("enabled", True):
        return mechanical, None
    try:
        hits = collect_hits(command, cfg)
        if not hits:
            raise ValueError("機械判定のヒットを再現できません")
        first = {"decision": hits[0]["decision"], "reason": hits[0]["reason"]}
        if first != {"decision": mechanical["decision"], "reason": mechanical["reason"]}:
            # ずれたまま判定すると deny が ask 未満に落ち得る(全件偽陽性 → 出力なし)
            raise ValueError("機械判定とヒットの集計が一致しません")
        if len(command) > LLM_MAX_COMMAND:
            raise ValueError(f"コマンドが長すぎます({len(command)}文字)")
        delimiter = "CMD-" + secrets.token_hex(16)
        if delimiter in command:
            raise ValueError("区切り文字列がコマンドと衝突しました")
        data = llm_judge.run_json(
            build_prompt(command, hits, delimiter),
            review_cfg.get("model", "sonnet"), LLM_TIMEOUT_SEC,
        )
        verdicts = parse_verdicts(data, hits)
    except llm_judge.Unavailable:
        return mechanical, None
    except Exception as exc:  # 判定不能は緩めない(機械判定を採用し、可視化する)
        detail = str(exc)[:LLM_REASON_MAX]  # 例外文に LLM 出力が入り得るので長さを抑える
        return mechanical, (
            f"[safe-dev-hooks] bash_guard: LLM判定に失敗したため機械判定を採用しました: {detail}"
        )
    return combine(hits, verdicts, mechanical)


def main() -> None:
    event = hook_io.read_event()
    if event.get("tool_name") != "Bash":
        sys.exit(0)
    cfg_all = config.load_config(event.get("cwd"))
    cfg = cfg_all.get("bash_guard", {})
    command = (event.get("tool_input") or {}).get("command", "")
    try:
        verdict = evaluate(command, cfg)
    except Exception as exc:  # deny層の判定不能は安全側に倒す(fail-close)
        hook_io.finalize(
            hook_io.pre_tool_decision(
                "ask",
                f"bash_guard の判定に失敗したため確認してください: {exc}",
            ),
            cfg_all,
        )
        return
    notice = None  # pragma: no mutate  (後段は truthiness だけを見るため "" と等価)
    if verdict is not None:
        verdict, notice = llm_review(command, cfg, verdict)
    out = hook_io.pre_tool_decision(verdict["decision"], verdict["reason"]) if verdict else None
    if notice:
        out = dict(out or {})
        out["systemMessage"] = notice
    hook_io.finalize(out, cfg_all)


if __name__ == "__main__":
    main()
