"""验证 error_origin 的判定：workspace 内 = package，测试目录 = tests。

单独抽出来是因为路径分隔符不一致曾导致全部误判——不能只靠肉眼测一次。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mutloop import runner  # noqa: E402

WS = Path("data/_ws").resolve()
SEP = "\\" if sys.platform == "win32" else "/"

# 用真实 workspace 前缀拼样例，避免手写绝对路径出错
PKG = str(WS / "marshmallow" / "schema.py")
TESTS = str(Path("subjects/marshmallow/tests/test_schema.py").resolve())

CASES = [
    (PKG + f":1069: ValueError: Invalid fields for", "package", "ValueError"),
    (PKG + f":59: TypeError: bad arg", "package", "TypeError"),
    (TESTS + f":560: Failed: DID NOT RAISE", "tests", "Failed"),
    (TESTS + f":253: assert 1 == 2", "tests", "AssertionError"),
    (TESTS + f":10: AssertionError: boom", "tests", "AssertionError"),
]

# 同一批用例，改成正斜杠，验证分隔符归一化
CASES_SLASH = [(c.replace(SEP, "/"), o, t) for c, o, t in CASES]


def run(cases, label: str) -> int:
    print(f"--- {label} ---")
    bad = 0
    for line, want_origin, want_type in cases:
        got_type, got_origin, _ = runner._parse_first_error(line, WS)
        ok = (got_origin == want_origin) and (got_type == want_type)
        bad += 0 if ok else 1
        tail = line.split(SEP)[-1].split("/")[-1][:34]
        print(f"  [{'OK ' if ok else 'ERR'}] origin={str(got_origin):<8} "
              f"type={str(got_type):<15} {tail}")
    return bad


if __name__ == "__main__":
    print(f"workspace = {WS}\n")
    b1 = run(CASES, "原生分隔符")
    print()
    b2 = run(CASES_SLASH, "全部正斜杠（验证归一化）")
    print()
    if b1 or b2:
        print(f"失败 {b1 + b2} 项")
        sys.exit(1)
    print("全部通过")
