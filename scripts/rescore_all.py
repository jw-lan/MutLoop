"""按最终口径重算全部模块的 summary（不重跑测试）。

用户 2026-09-02 拍板：
1. timeout 算 killed，且与 crash 同归「廉价 killed」
2. 主用传统口径（分子 = killed + timeout），严格口径作辅助

因此：
- JUDGED_STATUSES 新增 timeout（进分母）
- mutation_score 分子 = killed + timeout
- strict_mutation_score 分子 = 被杀死者 - 廉价者（crash + timeout）
- timeout 的 kill_class 需补成 KILL_CRASH（旧结果里是 None）

用法：
    python scripts/rescore_all.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mutloop.runner import (  # noqa: E402
    JUDGED_STATUSES, KILL_CRASH, MutantResult, classify_kill, summarize,
)

SKIP_PREFIX = ("_pc", "_serial", "_parallel")
SKIP_FILES = ("marshmallow-utils-rerun.json",)


def main() -> int:
    files = sorted(glob.glob(str(ROOT / "data" / "s2" / "*.json")))
    rows = []
    for f in files:
        b = os.path.basename(f)
        if b.startswith(SKIP_PREFIX) or b in SKIP_FILES:
            continue
        data = json.loads(Path(f).read_text(encoding="utf-8"))

        objs = []
        for r in data["results"]:
            o = MutantResult(**{k: v for k, v in r.items()
                                if k in MutantResult.__dataclass_fields__})
            # 重新定性：timeout 现在归为 KILL_CRASH
            o.kill_class = classify_kill(o)
            objs.append(o)

        data["summary"] = summarize(objs)
        data["results"] = [o.as_record() for o in objs]
        data["scoring_note"] = (
            "2026-09-02 定稿：timeout 计入 killed（JUDGED_STATUSES 含 timeout）；"
            "传统口径分子 = killed + timeout；严格口径剔除 crash + timeout。"
        )
        Path(f).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        s = data["summary"]
        rows.append((b.replace(".json", ""), s))

    print(f"重算完成 {len(rows)} 个模块\n")
    print(f"{'模块':<28}{'可判定':>7}{'killed':>7}{'sv':>5}{'still':>6}{'time':>6}"
          f"{'传统':>8}{'严格':>8}")
    print("-" * 78)
    TK = TS = TT = TB = TC = 0
    for name, s in sorted(rows, key=lambda x: x[1]["judged_mutants"]):
        st = s["by_status"]
        k, sv, sb, tm = (st.get("killed", 0), st.get("survived", 0),
                         st.get("stillborn", 0), st.get("timeout", 0))
        TK += k; TS += sv; TT += tm; TB += sb
        TC += s["kill_classes"].get(KILL_CRASH, 0)
        print(f"{name:<28}{s['judged_mutants']:>7}{k:>7}{sv:>5}{sb:>6}{tm:>6}"
              f"{s['mutation_score']:>8}{s['strict_mutation_score']:>8}")
    print("-" * 78)
    judged = TK + TS + TT
    print(f"{'合计':<28}{judged:>7}{TK:>7}{TS:>5}{TB:>6}{TT:>6}"
          f"{round(100*(TK+TT)/judged, 2):>8}"
          f"{round(100*(TK+TT-TC)/judged, 2):>8}")
    print()
    print("口径说明：传统 = (killed+timeout)/可判定；严格 = 剔除 crash+timeout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
