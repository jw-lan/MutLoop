"""被测项目注册表。

每个被测项目都锁定到一个明确的 release tag，保证实验可复现。
字段说明见 Subject 的 docstring。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBJECTS_DIR = ROOT / "subjects"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"

# Subject 是 frozen dataclass，检测结果放模块级缓存
_HYPOTHESIS_CACHE: dict[str, bool] = {}


@dataclass(frozen=True)
class Subject:
    """一个被测项目。

    name        : MutLoop 内部使用的短名
    repo        : GitHub 仓库（owner/repo）
    tag         : 锁定的 release tag（可复现的唯一凭据）
    package     : 相对项目根的源码包目录（src 布局下通常是 src/<pkg>）
    test_paths  : 测试目录 / 文件
    """

    name: str
    repo: str
    tag: str
    package: str
    test_paths: tuple[str, ...] = ("tests",)
    # 基线就失败的用例（多为平台相关，如 Windows 下的 symlink / 编码）。
    # 必须剔除：变异判定依赖"测试由绿转红"，预存失败会污染 kill 信号。
    deselect: tuple[str, ...] = ()
    # 允许覆盖项目根目录（S6 用 git worktree 对历史版本跑变异测试）。
    # 默认 None = 用 subjects/ 下的锁定 tag 版本。
    root_override: Path | None = None

    @property
    def root(self) -> Path:
        if self.root_override is not None:
            return self.root_override
        return SUBJECTS_DIR / self.name

    @property
    def package_dir(self) -> Path:
        return self.root / self.package

    @property
    def top_module(self) -> str:
        return Path(self.package).name

    def exists(self) -> bool:
        return self.package_dir.is_dir()

    @property
    def uses_hypothesis(self) -> bool:
        """测试里是否用了 hypothesis 属性测试。

        属性测试每次随机生成用例，导致**同一个变异体两次判定可能不同**——
        实测 attrs/validators.py 的 60 个变异体里就有 2 个因此翻转
        （L182 killed→survived、L376 killed→stillborn）。
        可复现性是变异测试的生命线，所以这类项目必须固定 seed。
        """
        if self.name in _HYPOTHESIS_CACHE:
            return _HYPOTHESIS_CACHE[self.name]
        hit = False
        for t in self.test_paths:
            d = self.root / t
            if not d.is_dir():
                continue
            for p in d.rglob("*.py"):
                try:
                    if "hypothesis" in p.read_text(encoding="utf-8", errors="ignore"):
                        hit = True
                        break
                except OSError:
                    continue
            if hit:
                break
        _HYPOTHESIS_CACHE[self.name] = hit
        return hit


SUBJECTS: dict[str, Subject] = {
    s.name: s
    for s in (
        Subject("attrs", "python-attrs/attrs", "25.3.0", "src/attr"),
        Subject("click", "pallets/click", "8.1.8", "src/click",
                deselect=(
                    "tests/test_types.py::test_path_resolve_symlink",      # WinError 1413 符号链接
                    "tests/test_utils.py::test_filename_formatting",       # Windows 控制台编码
                )),
        Subject("jinja", "pallets/jinja", "3.1.6", "src/jinja2"),
        Subject("marshmallow", "marshmallow-code/marshmallow", "3.26.2", "src/marshmallow",
                deselect=(
                    "tests/test_utils.py::test_from_timestamp_with_overflow_value",  # Windows 时间戳溢出
                )),
        Subject("dateutil", "dateutil/dateutil", "2.9.0.post0", "src/dateutil",
                deselect=(
                    # Windows 简体中文区域：tzwin 返回本地化的时区名（"东部标准时间"），
                    # 测试断言的是英文原名，属平台/locale 差异，非代码缺陷
                    "tests/test_tz.py::TzWinTest::testTzResLoadName",
                    "tests/test_tz.py::TzWinTest::testTzResNameFromString",
                    "tests/test_tz.py::TzWinTest::testTzwinName",
                    "tests/test_tz.py::TzWinTest::testTzwinTimeOnlyTZName",
                )),
        Subject("requests", "psf/requests", "v2.34.2", "src/requests"),
    )
}

# 参与正式实验的候选（requests 因测试强依赖网络 httpbin，仅作排除论证）
CANDIDATES: tuple[str, ...] = ("attrs", "click", "jinja", "marshmallow", "dateutil")


def get(name: str) -> Subject:
    try:
        return SUBJECTS[name]
    except KeyError:
        raise SystemExit(
            f"未知被测项目: {name!r}，可选: {', '.join(sorted(SUBJECTS))}"
        ) from None


def with_root(subj: Subject, root: Path) -> Subject:
    """返回一个 root 指向自定义目录的 Subject 副本。

    S6 用 git worktree 对"修复前/修复后"历史版本跑变异测试时，
    用它把被测项目根指到 worktree，其余（package、deselect 等）保持不变。
    """
    from dataclasses import replace
    return replace(subj, root_override=Path(root))
