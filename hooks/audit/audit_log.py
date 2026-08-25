#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""全ツール実行・セッション境界をJSONLで監査記録する。失敗しても開発を止めない。"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from hooks.lib import config, hook_io  # noqa: E402

SUMMARY_MAX_CHARS = 500

# --- tool_summary の構造保存型トランケーション -----------------------------
#
# 旧実装は json.dumps(tool_input)[:SUMMARY_MAX_CHARS] のように直列化後の文字列を
# 単純スライスしていたため、切れ目が文字列リテラルの途中に落ちると壊れたJSONに
# なっていた(実測: 17日分のBashレコードの15%がjson.loads不能、すべて500文字ちょうど
# で頭打ちになっていたことから原因はこの単純スライスと特定)。
#
# build_tool_summary() は逆に「直列化する前」に構造(キー数・値の長さ・入れ子の深さ)
# を切り詰めるため、返す文字列は常に json.loads できる(そのことをテストで固定する)。
# 切り詰めが発生した箇所には TRUNCATED_MARKER_KEY を埋め込み、読者が「全文を見ている」
# と誤解しないようにする。
#
# マーカー形式(このモジュールの外で tool_summary を解析するツール向けの契約):
#   - 文字列値の末尾切り詰め: 元の文字列の後ろに TRUNCATED_TAG_PREFIX + 省略した
#     文字数 + "c]" を付与する(例: "...[+120c]" の "..." 部分は実際には "…"。
#     空白は入らない)。正規表現なら `re.search(r"…\[\+\d+c\]$", value)` で検出できる。
#   - dict/list のうちキー/要素を間引いた場合: その階層に OMITTED_KEYS_KEY(dict)
#     / OMITTED_ITEMS_KEY(list に混ぜる {"__omitted_items__": N} 要素)で
#     省略した個数を記録する。
#   - 何らかの切り詰めが発生した dict/list には、その階層に
#     TRUNCATED_MARKER_KEY: true を追加する。tool_input が非dict/非listの単一値
#     (文字列・数値等)だった場合はこのキーを追加できないため、値そのものに埋め込む
#     末尾タグ(上記)のみが切り詰めの印になる。
#   - どうしても収まらない病的な入力(巨大なキー名が大量にある等)に対する最終手段
#     として、{"__audit_truncated__": true} のみの最小オブジェクトを返す。
#
# サイズの収束方式: 葉の文字数上限(leaf_chars)は「収まる最大値」を二分探索で
# 直接求める(実測した直列化後の長さで判定するため、JSONエスケープでバックスラッシュ
# 等が2倍に膨らむケースも取りこぼさない)。leaf_chars=0でも収まらない場合のみ、
# キー/要素数の上限(max_breadth)とキー名の上限(max_key_chars)を段階的に縮小して
# やり直す。
_MAX_BREADTH_INITIAL = 12
_MAX_BREADTH_FLOOR = 1
_MAX_KEY_CHARS_INITIAL = 80
_MAX_KEY_CHARS_FLOOR = 20
_MAX_DEPTH = 4
_SHRINK_STEPS = 10  # max_breadthは12→…→floor 1まで高々4段で収束するための十分な余裕

TRUNCATED_TAG_PREFIX = "…[+"  # 値末尾の切り詰めマーカーの接頭辞("…[+Nc]"。空白は入らない)
TRUNCATED_MARKER_KEY = "__audit_truncated__"
OMITTED_KEYS_KEY = "__omitted_keys__"
OMITTED_ITEMS_KEY = "__omitted_items__"
_FALLBACK_SUMMARY = json.dumps({TRUNCATED_MARKER_KEY: True}, ensure_ascii=False)


def _truncate_str(s: str, limit: int) -> tuple[str, bool]:
    """文字列をlimit文字以内に切り詰め、切り詰めたかどうかを返す。"""
    if len(s) <= limit:
        return s, False
    if limit <= 0:
        return f"{TRUNCATED_TAG_PREFIX}{len(s)}c]", True
    omitted = len(s) - limit
    return f"{s[:limit]}{TRUNCATED_TAG_PREFIX}{omitted}c]", True


def _int_digits_limit() -> int:
    """str(int)/json.dumps(int)を安全に呼べる最大桁数(概算)を返す。

    CPython 3.11+ は int<->str 変換に sys.get_int_max_str_digits()(既定4300)の
    上限を持ち、超えると ValueError を送出する(str(value)へのフォールバックでも
    同じ例外が起きるため、変換を試みる前に避ける必要がある)。3.10以前はこの
    仕組み自体が無いため無制限だが、そもそも監査ログの表示にそれほどの桁数は
    不要なので1000桁を上限とする。
    """
    getter = getattr(sys, "get_int_max_str_digits", None)
    if getter is None:
        return 1000  # 3.10: 無制限(このAPI自体が無い)
    limit = getter()
    if limit == 0:  # 0 は sys.set_int_max_str_digits(0) による「無制限」設定
        return 1000
    return max(50, limit - 300)  # 既定4300に対し余裕を持たせる


def _safe_int_str(value: int) -> tuple[str, bool]:
    """intを安全に文字列化する(str()のCPython変換上限によるValueErrorを送出しない)。

    戻り値は (文字列化した表現, プレースホルダーに倒したか) のタプル。呼び出し側は
    2つ目の要素だけで判定でき、桁数を確かめるためだけに再度 str(value) を呼ぶ必要が
    ない(それ自体が同じ ValueError を送出しうるため)。桁数の概算は
    value.bit_length() から求める(巨大な文字列を作らないので _int_digits_limit()
    を超えるintに対しても安全)。_cap_value の数値分岐と _cap_dict のキー変換の
    両方から共有する、intを文字列化する唯一の経路。
    """
    digits = int(value.bit_length() * 0.3010299956639812) + 1  # log10(2) ≈ 0.30103
    if digits > _int_digits_limit():
        sign = "-" if value < 0 else ""
        return f"{sign}<int ~{digits}digits>", True
    return str(value), False


def _safe_int_repr(value: int, leaf_chars: int) -> tuple[str, bool]:
    """巨大intをstr()/json.dumps()に一切通さずに切り詰め表現へ変換する。"""
    rendered, _is_placeholder = _safe_int_str(value)
    return _truncate_str(rendered, leaf_chars)


def _cap_value(value, depth: int, leaf_chars: int, max_breadth: int, max_key_chars: int):
    """任意のJSON値を(値, 切り詰めたか)のタプルとして返す。深さ・幅・葉の長さを制限する。"""
    if depth >= _MAX_DEPTH and isinstance(value, (dict, list)):
        try:
            preview = json.dumps(value, ensure_ascii=False, default=str)
        except RecursionError:
            preview = "<deeply nested>"
        except (TypeError, ValueError):
            preview = str(value)
        return _truncate_str(preview, leaf_chars)
    if isinstance(value, dict):
        return _cap_dict(value, depth, leaf_chars, max_breadth, max_key_chars)
    if isinstance(value, list):
        return _cap_list(value, depth, leaf_chars, max_breadth, max_key_chars)
    if isinstance(value, str):
        return _truncate_str(value, leaf_chars)
    if value is None or isinstance(value, bool):
        return value, False
    if isinstance(value, int):
        # str(int)自体が桁数上限でValueErrorを送出し得るため、_safe_int_str()経由でのみ
        # 文字列化する(直接 str(value) を呼ばない。再判定のための str(value) 呼び出しも
        # 同じ理由で行わない)。
        rendered, is_placeholder = _safe_int_str(value)
        if not is_placeholder and len(rendered) <= leaf_chars:
            return value, False
        return _truncate_str(rendered, leaf_chars)
    if isinstance(value, float):
        rendered = repr(value)  # NaN/Infinityを含め例外を出さない
        if len(rendered) <= leaf_chars:
            return value, False
        return _truncate_str(rendered, leaf_chars)
    # tool_input はJSONデコード結果のみを想定するためここには通常到達しないが、
    # 未知の型が来ても例外を出さず文字列化して切り詰める(保険)。
    return _truncate_str(str(value), leaf_chars)


def _cap_dict(d: dict, depth: int, leaf_chars: int, max_breadth: int, max_key_chars: int):
    keys = list(d.keys())
    kept_keys = keys[:max_breadth]
    omitted = len(keys) - len(kept_keys)
    truncated = omitted > 0
    out: dict = {}
    seen: set[str] = set()
    for raw_key in kept_keys:
        if isinstance(raw_key, str):
            key_str = raw_key
        elif isinstance(raw_key, int) and not isinstance(raw_key, bool):
            # bool は int のサブクラスだが True/False の文字列化は安全なので除外する。
            key_str, _is_placeholder = _safe_int_str(raw_key)
        else:
            key_str = str(raw_key)
        key_str, key_truncated = _truncate_str(key_str, max_key_chars)
        truncated = truncated or key_truncated
        base, i = key_str, 1
        while key_str in seen:
            key_str = f"{base}#{i}"
            i += 1
        seen.add(key_str)
        value, value_truncated = _cap_value(
            d[raw_key], depth + 1, leaf_chars, max_breadth, max_key_chars
        )
        truncated = truncated or value_truncated
        out[key_str] = value
    if omitted > 0:
        out[OMITTED_KEYS_KEY] = omitted
    if truncated:
        out[TRUNCATED_MARKER_KEY] = True
    return out, truncated


def _cap_list(lst: list, depth: int, leaf_chars: int, max_breadth: int, max_key_chars: int):
    kept = lst[:max_breadth]
    omitted = len(lst) - len(kept)
    truncated = omitted > 0
    out = []
    for item in kept:
        value, value_truncated = _cap_value(
            item, depth + 1, leaf_chars, max_breadth, max_key_chars
        )
        truncated = truncated or value_truncated
        out.append(value)
    if omitted > 0:
        out.append({OMITTED_ITEMS_KEY: omitted})
    if truncated:
        out.append({TRUNCATED_MARKER_KEY: True})
    return out, truncated


def _serialize(value, leaf_chars: int, max_breadth: int, max_key_chars: int) -> str:
    capped, _truncated = _cap_value(value, 0, leaf_chars, max_breadth, max_key_chars)
    return json.dumps(capped, ensure_ascii=False)


def build_tool_summary(tool_input) -> str:
    """tool_input から、常にjson.loads可能でSUMMARY_MAX_CHARS以内のtool_summary文字列を作る。

    典型ケース(単一の長いコマンドなど)ではSUMMARY_MAX_CHARSぎりぎりまで内容を見せたい
    ので、まずleaf_chars=SUMMARY_MAX_CHARS(切り詰め無しに近い)を試し、収まらなければ
    収まる最大のleaf_charsを二分探索で直接求める(半減ずつの当て推量だと、JSON外周の
    オーバーヘッドを見込んでいないため本来使えるはずの予算を無駄にしてしまう)。
    leaf_chars=0でも収まらない病的な入力(キー数・キー名自体が過大)にだけ、
    キー/要素数の上限とキー名の上限を段階的に縮小してやり直す。最終手段として
    _FALLBACK_SUMMARY(妥当なJSON・SUMMARY_MAX_CHARS未満)を返す。この不変条件
    (常に妥当なJSON・常にSUMMARY_MAX_CHARS以内)はテストで固定している。
    """
    value = tool_input if tool_input else {}
    max_breadth = _MAX_BREADTH_INITIAL
    max_key_chars = _MAX_KEY_CHARS_INITIAL
    for _ in range(_SHRINK_STEPS):
        serialized = _serialize(value, SUMMARY_MAX_CHARS, max_breadth, max_key_chars)
        if len(serialized) <= SUMMARY_MAX_CHARS:
            return serialized
        floor_serialized = _serialize(value, 0, max_breadth, max_key_chars)
        if len(floor_serialized) > SUMMARY_MAX_CHARS:
            if max_breadth > _MAX_BREADTH_FLOOR:
                max_breadth = max(_MAX_BREADTH_FLOOR, max_breadth // 2)
                max_key_chars = max(_MAX_KEY_CHARS_FLOOR, max_key_chars // 2)
                continue
            return _FALLBACK_SUMMARY
        # leaf_chars=0 は収まり、leaf_chars=SUMMARY_MAX_CHARS は収まらない
        # → 収まる最大の leaf_chars を二分探索する(実測した長さで判定)。
        lo, hi = 0, SUMMARY_MAX_CHARS
        best = floor_serialized
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = _serialize(value, mid, max_breadth, max_key_chars)
            if len(candidate) <= SUMMARY_MAX_CHARS:
                lo = mid
                best = candidate
            else:
                hi = mid - 1
        return best
    return _FALLBACK_SUMMARY


def main() -> None:
    event = hook_io.read_event()
    # notices=False: このフックは通知を表示しない(finalize の quiet_notices=True)。
    # 通知を生成させると、表示しないままクールダウン枠だけを消費してしまう。
    # audit_log は SessionStart と全 PreToolUse/PostToolUse で走る最頻フックなので、
    # そうなると以後 1 時間、対話フック側の通知がまるごと抑止される。
    cfg_all = config.load_config(event.get("cwd"), notices=False)
    cfg = cfg_all.get("audit_log", {})
    if not cfg.get("enabled", True):
        hook_io.finalize(None, cfg_all, quiet_notices=True)
    try:
        log_dir = Path(cfg.get("path", ".claude/logs"))
        if not log_dir.is_absolute():
            log_dir = Path(config.project_root(event.get("cwd")) or ".") / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc)
        record = {
            "ts": now.isoformat(),
            "session_id": event.get("session_id", ""),
            "event": event.get("hook_event_name", ""),
            "tool_name": event.get("tool_name", ""),
            "tool_summary": build_tool_summary(event.get("tool_input")),
        }
        log_file = log_dir / f"audit-{now.strftime('%Y%m%d')}.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 監査ログの失敗は開発を止めない(spec セクション8)
    hook_io.finalize(None, cfg_all, quiet_notices=True)


if __name__ == "__main__":
    main()
