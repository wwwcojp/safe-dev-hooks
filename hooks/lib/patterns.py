"""ルールJSONの読込と、テキストへのパターン適用(バリデータ付き)。"""
import json
import re
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def load_rules(name: str):
    return json.loads((RULES_DIR / name).read_text(encoding="utf-8"))


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 13:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mynumber_ok(digits: str) -> bool:
    """マイナンバーのチェックデジット検証(J-LIS方式)。"""
    if len(digits) != 12 or not digits.isdigit():
        return False
    p = digits[:11][::-1]  # 検査用数字を除く11桁を末尾から
    total = sum(
        int(p[n - 1]) * ((n + 1) if n <= 6 else (n - 5)) for n in range(1, 12)
    )
    rem = total % 11
    check = 0 if rem <= 1 else 11 - rem
    return check == int(digits[11])


_VALIDATORS = {"luhn": luhn_ok, "mynumber": mynumber_ok}


def scan_text(text: str, rules: list) -> list:
    findings = []
    for rule in rules:
        for m in re.finditer(rule["regex"], text):
            validator = _VALIDATORS.get(rule.get("validator", ""))
            if validator is not None:
                digits = re.sub(r"\D", "", m.group())
                if not validator(digits):
                    continue
            findings.append({"rule": rule["name"], "match": m.group()})
            break  # 1ルールにつき1件で十分
    return findings
