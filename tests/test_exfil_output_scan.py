from helpers import load_hook

scan = load_hook("post_tool_use/exfil_output_scan.py")

CFG_WARN = {"enabled": True, "action": "warn"}
CFG_REDACT = {"enabled": True, "action": "redact"}


def test_detects_secret_in_output():
    findings = scan.evaluate("結果: AKIAIOSFODNN7EXAMPLE", CFG_WARN)
    assert any(f["rule"] == "aws-access-key" for f in findings)


def test_warn_builds_additional_context():
    findings = scan.evaluate("AKIAIOSFODNN7EXAMPLE", CFG_WARN)
    out = scan.build_output(findings, "AKIAIOSFODNN7EXAMPLE", CFG_WARN)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "aws-access-key" in ctx
    assert "AKIAIOSFODNN7EXAMPLE" not in ctx  # 値は再掲しない


def test_redact_masks_value():
    raw = "key=AKIAIOSFODNN7EXAMPLE end"
    findings = scan.evaluate(raw, CFG_REDACT)
    out = scan.build_output(findings, raw, CFG_REDACT)
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert "AKIAIOSFODNN7EXAMPLE" not in updated
    assert "[REDACTED:aws-access-key]" in updated


def test_redact_falls_back_to_warn_for_non_string(capsys):
    findings = [{"rule": "aws-access-key", "match": "AKIAIOSFODNN7EXAMPLE"}]
    out = scan.build_output(findings, {"nested": "value"}, CFG_REDACT)
    assert "additionalContext" in out["hookSpecificOutput"]


def test_clean_output_returns_no_findings():
    assert scan.evaluate("普通の応答です", CFG_WARN) == []


def test_gitleaks_finding_redacted(monkeypatch):
    monkeypatch.setattr(
        scan.scanners, "scan_secrets",
        lambda text, sc, cwd: [{"rule": "gitleaks:x", "match": "STUBSECRET"}],
    )
    raw = "leak=STUBSECRET end"
    findings = scan.evaluate(raw, CFG_REDACT, {"gitleaks": "auto"}, None)
    out = scan.build_output(findings, raw, CFG_REDACT)
    updated = out["hookSpecificOutput"]["updatedToolOutput"]
    assert "STUBSECRET" not in updated
    assert "[REDACTED:gitleaks:x]" in updated
