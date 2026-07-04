from lib import patterns


def test_luhn_valid_and_invalid():
    assert patterns.luhn_ok("4111111111111111") is True
    assert patterns.luhn_ok("4111111111111112") is False
    assert patterns.luhn_ok("abc") is False


def test_mynumber_check_digit():
    # 11桁 "12345678901" のチェックデジットは 8(仕様書のアルゴリズム参照)
    assert patterns.mynumber_ok("123456789018") is True
    assert patterns.mynumber_ok("123456789012") is False
    assert patterns.mynumber_ok("12345") is False


def test_scan_detects_aws_key():
    rules = patterns.load_rules("secret_patterns.json")
    hits = patterns.scan_text("key=AKIAIOSFODNN7EXAMPLE ok", rules)
    assert any(h["rule"] == "aws-access-key" for h in hits)


def test_scan_detects_private_key_block():
    rules = patterns.load_rules("secret_patterns.json")
    hits = patterns.scan_text("-----BEGIN RSA PRIVATE KEY-----", rules)
    assert any(h["rule"] == "private-key-block" for h in hits)


def test_scan_generic_credential_assignment():
    rules = patterns.load_rules("secret_patterns.json")
    hits = patterns.scan_text('API_KEY = "supersecretvalue123"', rules)
    assert any(h["rule"] == "generic-credential" for h in hits)


def test_scan_clean_text_no_hits():
    rules = patterns.load_rules("secret_patterns.json")
    assert patterns.scan_text("普通のテキストです", rules) == []


def test_pii_credit_card_requires_luhn():
    rules = patterns.load_rules("pii_patterns.json")
    assert any(h["rule"] == "credit-card" for h in patterns.scan_text("4111 1111 1111 1111", rules))
    assert not any(
        h["rule"] == "credit-card" for h in patterns.scan_text("4111 1111 1111 1112", rules)
    )


def test_pii_mynumber_requires_check_digit():
    rules = patterns.load_rules("pii_patterns.json")
    assert any(h["rule"] == "my-number" for h in patterns.scan_text("番号: 123456789018", rules))
    text_invalid = "番号: 123456789012"
    assert not any(
        h["rule"] == "my-number" for h in patterns.scan_text(text_invalid, rules)
    )


def test_pii_email_and_phone():
    rules = patterns.load_rules("pii_patterns.json")
    text = "連絡先: taro@example.co.jp / 090-1234-5678"
    got = {h["rule"] for h in patterns.scan_text(text, rules)}
    assert {"email", "jp-phone"} <= got


def test_confidential_markers_file_shape():
    data = patterns.load_rules("confidential_markers.json")
    assert "社外秘" in data["markers"]
    assert "confidential" in data["markers"]


def test_scan_collects_all_distinct_matches_per_rule():
    rules = patterns.load_rules("secret_patterns.json")
    text = "a=AKIAIOSFODNN7EXAMPLE b=AKIAZZZZZZZZZZZZZZZZ"
    hits = [h for h in patterns.scan_text(text, rules) if h["rule"] == "aws-access-key"]
    assert {h["match"] for h in hits} == {"AKIAIOSFODNN7EXAMPLE", "AKIAZZZZZZZZZZZZZZZZ"}


def test_scan_dedupes_identical_matches():
    rules = patterns.load_rules("secret_patterns.json")
    text = "x=AKIAIOSFODNN7EXAMPLE y=AKIAIOSFODNN7EXAMPLE"
    hits = [h for h in patterns.scan_text(text, rules) if h["rule"] == "aws-access-key"]
    assert len(hits) == 1
