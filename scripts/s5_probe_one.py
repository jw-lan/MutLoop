"""S5 探针：给定一个存活变异体，让 LLM 反复生成测试直到杀死或放弃。

流程（最多 5 轮，多轮对话）：
1. 第一轮让 LLM 针对变异编写测试（**不给出等价出口**）。
2. 运行观察：
   - 杀死（原代码 PASS + 变异代码 FAIL）→ 保留测试用例，结束。
   - 程序报错（对原代码 FAIL / 重言式断言）→ 把错误信息反馈给 LLM，进入下一轮。
   - 变异存活（杀不死）→ 告诉 LLM"没杀死"，并把 LLM 这轮写的测试还给它，进入下一轮。
3. 5 轮后仍存活 → 标记「疑似等价变异体」（**唯一允许的等价出口**），交用户决定。

状态：
- killed               —— 成功杀死，测试已保留
- suspected_equivalent —— 5 轮后仍存活，疑似等价
- error                —— 5 轮都生成失败（对原代码报错 / 重言式）

用法
----
    DEEPSEEK_API_KEY=sk-... python scripts/s5_probe_one.py \
        --project jinja --module environment.py --line 1605 --operator CR
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.mutator import enumerate_mutants  # noqa: E402
from mutloop.runner import in_package_path, prepare_workspace  # noqa: E402
from mutloop.subjects import get  # noqa: E402

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MAX_ROUNDS = 5


# ---------------------------------------------------------------------------
# 重言式/投机断言检测（纯 AST，不依赖 LLM）
# ---------------------------------------------------------------------------
def _ast_same(a, b) -> bool:
    return ast.dump(a) == ast.dump(b)


def tautological_asserts(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ["<语法错误，无法解析>"]
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        t = node.test
        msg = None
        if isinstance(t, ast.Constant) and isinstance(t.value, bool):
            msg = f"assert {t.value}（恒{'真' if t.value else '假'}）"
        elif isinstance(t, ast.Compare) and len(t.ops) == 1:
            op = t.ops[0]
            left, right = t.left, t.comparators[0]
            if _ast_same(left, right):
                if isinstance(op, (ast.Eq, ast.Is)):
                    msg = "自比恒真（x == x / x is x）"
                elif isinstance(op, (ast.NotEq, ast.IsNot)):
                    msg = "自比恒假（x != x / x is not x）"
            elif isinstance(left, ast.Constant) and isinstance(right, ast.Constant):
                try:
                    lv, rv = left.value, right.value
                    if isinstance(op, ast.Eq) and lv == rv:
                        msg = f"字面量恒等（{lv!r} == {rv!r}）"
                    elif isinstance(op, ast.NotEq) and lv != rv:
                        msg = f"字面量恒不等（{lv!r} != {rv!r}）"
                except Exception:
                    pass
        if msg:
            findings.append(msg)
    return findings


# ---------------------------------------------------------------------------
# LLM 调用（多轮对话）
# ---------------------------------------------------------------------------
from mutloop.mutator import enumerate_mutants  # noqa: E402
from mutloop.runner import in_package_path, prepare_workspace  # noqa: E402
from mutloop.subjects import DATA_DIR, get  # noqa: E402


def _import_path(subj, module: str) -> str:
    return f"{subj.top_module}.{Path(module).stem}"


def _common_rules(ipath: str) -> str:
    """三臂共用的输出规则（严格一致，保证公平对比）。"""
    return f"""
要求（严格遵守）：
1. 只输出测试代码（```python 代码块包裹），不要任何解释。
2. **不要判定这个变异是否等价**——你的任务就是尽可能生成能杀死它的测试。
3. 测试必须真实调用被测代码并断言其可观察行为；禁止恒真/恒假断言
   （如 assert True、assert 1 == 1、assert x == x）。
4. **import 必须真实存在**：从 `{ipath}` 导入被测的类/函数，不要写占位符
   （如 `from your_module import ...`），不要用错误的模块路径。"""


def covering_tests(project: str, module_key: str, line: int, limit: int = 20):
    """从行级覆盖索引取覆盖该行的测试列表（截断到 limit，避免 prompt 过长）。"""
    p = DATA_DIR / "s3" / f"line_index_{project}.json"
    if not p.exists():
        return [], 0
    idx = json.loads(p.read_text(encoding="utf-8"))
    tests = idx.get(module_key, {}).get(str(line), [])
    return tests[:limit], len(tests)


def build_prompt(arm: str, subj, module: str, target, orig_lines: list[str],
                 line: int, ctx: int = 20, test_limit: int = 20) -> str:
    """三臂的 prompt。**严格隔离**：三套 prompt 各自从零构建，不共享任何上下文，
    信息量递减：臂1 完整变异内容 → 臂2 覆盖测试列表 → 臂3 仅模块名+行号。"""
    ipath = _import_path(subj, module)
    header = (
        f"你是软件测试工程师，参与变异测试（mutation testing）。\n\n"
        f"被测项目：{subj.name}　被测模块：`{ipath}`"
        f"（import 路径，例如 `from {ipath} import ...`）\n"
        f"被测位置：{module} 第 {line} 行\n\n"
        f"已知：该位置存在一个「变异」（原代码被做了一处改动）。\n"
        f"你的任务只有一个：**写一个测试，使得对原代码 PASS、对变异后的代码 FAIL**"
        f"（即「杀死」这个变异体）。"
    )
    rules = _common_rules(ipath)

    if arm == "directed":
        # 臂 1：完整变异内容。用「带真实行号的代码」而非 unified_diff——
        # unified_diff 的 n=3 只显示变更 ±3 行（7 行），会把方法签名裁掉，
        # 导致 LLM 不知道这段代码在哪个方法里、该怎么调用触发。
        mut_lines = target.source().splitlines()
        lo = max(0, line - 1 - ctx)
        hi = min(len(orig_lines), line + ctx)
        # 往上找最近的 def（方法）和 class（类）边界，让 LLM 知道这段代码的所属
        def_idx = None
        cls_idx = None
        for i in range(line - 1, max(0, line - 150), -1):
            st = orig_lines[i].strip()
            if def_idx is None and st.startswith(("def ", "async def ")):
                def_idx = i
            elif cls_idx is None and st.startswith("class "):
                cls_idx = i
            if def_idx is not None and cls_idx is not None:
                break
        code_lines = []
        for i in range(lo, hi):
            no = i + 1
            o = orig_lines[i]
            m = mut_lines[i] if i < len(mut_lines) else o
            if o != m:
                code_lines.append(f"{no:>5} | - {o}")
                code_lines.append(f"{no:>5} | + {m}")
            else:
                code_lines.append(f"{no:>5} |   {o}")
        code = "\n".join(code_lines)
        encl = ""
        if cls_idx is not None and def_idx is not None:
            encl = (f"\n这段代码位于「{orig_lines[cls_idx].strip()}」类的"
                    f"「{orig_lines[def_idx].strip()}」方法内。\n"
                    f"要触发这段代码，请正确实例化/调用**这个类**的方法，"
                    f"并按方法签名构造参数（注意类名，别用错类）。")
        elif def_idx is not None:
            encl = (f"\n这段代码位于「{orig_lines[def_idx].strip()}」方法内。\n"
                    f"要触发这段代码，请根据该方法/类的签名构造正确的调用。")
        return header + f"""

变异算子：{target.operator}　变异描述：{target.description}
{encl}

变异点附近代码（`-` 原代码，`+` 变异后，行号为真实行号）：

```python
{code}
```
""" + rules

    if arm == "coverage":
        # 臂 2：只给覆盖该行的现有测试列表
        # 已知局限：测试名自带语义（如 test_weekday_zero_raises_value_error），
        # 会部分提示该行在测什么。这是臂 2 的固有特性，报告里如实注明。
        mk = str(in_package_path(subj, target.file)).replace("\\", "/")
        tests, total = covering_tests(subj.name, mk, line, test_limit)
        shown = "\n".join(f"- {t}" for t in tests) or "（该行没有覆盖测试记录）"
        tail = (f"\n（共 {total} 个覆盖测试，此处列出前 {len(tests)} 个）"
                if total > len(tests) else "")
        return header + f"""

**不提供变异内容**。已知覆盖第 {line} 行的现有测试如下：

{shown}{tail}

请基于上述测试的覆盖情况（它们提示了该行在测什么），写出能最敏感地捕捉该行行为变化的测试。
""" + rules

    if arm == "blind":
        # 臂 3：只给模块名 + 行号，没有任何变异或测试信息
        return header + """

**不提供变异内容，也不提供任何现有测试的信息**。

请仅基于被测模块的功能语义，针对该行所在的代码写一个测试。
""" + rules

    raise ValueError(f"未知 arm: {arm!r}（可选 directed / coverage / blind）")


def call_llm(api_key: str, model: str, messages: list[dict], max_tokens: int,
             thinking: str = "disabled", temperature: float = 0.2):
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "thinking": {"type": thinking},
        "temperature": temperature,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    m = d["choices"][0]["message"]
    return (m.get("content") or ""), d.get("usage", {})


def extract_code(content: str) -> str:
    m = re.search(r"```python\s*\n(.*?)```", content, re.S)
    if m:
        return m.group(1)
    m = re.search(r"```\s*\n(.*?)```", content, re.S)
    if m:
        return m.group(1)
    return content.strip()


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def run_test(subj, test_file: Path, workspace_pythonpath: str | None):
    env = dict(os.environ)
    if workspace_pythonpath:
        env["PYTHONPATH"] = workspace_pythonpath
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    args = [sys.executable, "-m", "pytest", str(test_file),
            "-p", "no:cacheprovider", "--no-header", "-q", "--tb=short"]
    proc = subprocess.run(args, cwd=str(subj.root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=300)
    return proc.returncode, (proc.stdout or "").strip()


def _save_test(project: str, module: str, mutant_id: str, code: str) -> Path:
    """把杀死变异体的测试用例保留下来（S5 的产出）。"""
    d = ROOT / "data" / "s5" / "generated_tests"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{project}_{Path(module).stem}_L{mutant_id[:8]}.py"
    f.write_text(code, encoding="utf-8")
    return f


def probe_one(project: str, module: str, line: int, operator: str, *,
              api_key: str, arm: str = "directed", root=None,
              model: str = "deepseek-v4-flash", max_tokens: int = 4000,
              thinking: str = "disabled",
              max_rounds: int = MAX_ROUNDS) -> dict:
    """对一个存活变异体做完整探针。

    arm: directed=臂1 给完整变异 diff；coverage=臂2 只给覆盖该行的测试列表；
         blind=臂3 只给模块名+行号。三臂 prompt 严格隔离，其余流程完全一致。
    root: 非空时在被测项目的历史版本（S6 的 git worktree）上补测。
    """
    subj = get(project)
    if root is not None:
        from mutloop.subjects import with_root
        subj = with_root(subj, root)
    p = subj.package_dir / module
    orig = p.read_text(encoding="utf-8")
    orig_lines = orig.splitlines()

    target = None
    for m in enumerate_mutants(p):
        if m.line == line and m.operator == operator:
            target = m
            break
    if target is None:
        return {"status": "error", "reason": f"找不到变异体 L{line} {operator}"}

    base = {"mutant_id": target.mutant_id, "project": project, "module": module,
            "line": line, "operator": operator, "description": target.description,
            "arm": arm}

    # 预生成变异代码的工作副本（跑变异代码时用）
    ws = prepare_workspace(subj, ROOT / "data" / "_s5probe" / subj.name)
    dest = ws / subj.package_dir.name / in_package_path(subj, target.file)
    dest.write_text(target.source(), encoding="utf-8")

    test_file = subj.root / "tests" / "test_s5_probe_tmp.py"

    messages = [{"role": "user", "content": build_prompt(
        arm, subj, module, target, orig_lines, line)}]

    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    last_code = ""
    taut_count = 0  # 重言式断言出现的轮次数（作弊率分子）

    for rnd in range(1, max_rounds + 1):
        content, usage = call_llm(api_key, model, messages, max_tokens, thinking=thinking)
        for k in total_usage:
            total_usage[k] = total_usage.get(k, 0) + (usage.get(k, 0))
        if not content.strip():
            return {**base, "status": "error", "reason": "LLM 返回空 content",
                    "rounds": rnd, "tautological_rounds": taut_count,
                    "usage": total_usage}

        code = extract_code(content)
        last_code = code
        if not code.strip():
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "你只输出了说明，没有输出测试代码。请直接输出 ```python 代码块。".strip()})
            continue

        taut = tautological_asserts(code)
        if taut:
            taut_count += 1
        test_file.write_text(code, encoding="utf-8")
        rc_orig, out_orig = run_test(subj, test_file, workspace_pythonpath=None)

        # 程序报错 / 重言式 → 反馈错误，进入下一轮
        if taut or rc_orig != 0:
            if taut:
                why = "含重言式断言：" + "; ".join(taut)
            else:
                why = f"对原始代码运行就报错了（rc={rc_orig}）：\n{out_orig[-800:]}"
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": (
                f"你生成的测试无效：{why}\n"
                f"这是测试本身的问题，不是变异引起的。请修复测试，重新输出 ```python 代码块。")})
            continue

        # 跑变异代码
        rc_mut, out_mut = run_test(subj, test_file, workspace_pythonpath=str(ws))

        if rc_mut != 0:
            # 杀死 → 保留测试，结束
            saved = _save_test(project, module, target.mutant_id, code)
            return {**base, "status": "killed", "code": code, "rounds": rnd,
                    "tautological_rounds": taut_count,
                    "saved_test": str(saved), "usage": total_usage}

        # 存活 → 告诉 LLM 没杀死，把测试还给它，进入下一轮
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": (
            f"你生成的测试没能杀死这个变异——对变异后的代码也通过了（全部 PASS）。\n"
            f"这是你写的测试：\n\n```python\n{code}\n```\n\n"
            f"请重新生成一个更强的测试，务必区分原代码和变异代码。"
            f"注意：不要判定等价，继续尝试生成能杀死它的测试。")})

    # 5 轮后仍存活 → 疑似等价（LLM 无法判定）
    return {**base, "status": "suspected_equivalent", "code": last_code,
            "rounds": max_rounds, "tautological_rounds": taut_count,
            "usage": total_usage}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--module", required=True)
    ap.add_argument("--line", type=int, required=True)
    ap.add_argument("--operator", required=True)
    ap.add_argument("--arm", default="directed",
                    choices=["directed", "coverage", "blind"],
                    help="directed=臂1 完整变异diff；coverage=臂2 覆盖测试列表；"
                         "blind=臂3 仅模块名+行号")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--thinking", default="disabled", choices=["disabled", "enabled"])
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[错误] 缺少环境变量 DEEPSEEK_API_KEY")
        return 1

    r = probe_one(args.project, args.module, args.line, args.operator,
                  api_key=api_key, arm=args.arm,
                  model=args.model, max_tokens=args.max_tokens,
                  thinking=args.thinking, max_rounds=args.max_rounds)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
