"""⛔ 宪法的每一条都必须**有账可查**：三档相加等于总条数。

## 缺陷的形状（2026-08-03 实测）

`constitution.toml` 自己写着：

> ⛔ **必须逐条登记。** 每次判定的结论都会带上「未覆盖 N 条：…」的尾巴。
> 不登记它们，`summary()` 就会印出一句**没有尾巴的「无宪法命中」**——
> 而那句话会被读成「查过了，没事」，真相却是「查过的那部分没事，
> 还有 N 条根本没查」。**那句话本身就是假绿。**

⚠️ 实测：散文里 **18 条**，toml 里有机械判据的 **3 条**、登记为判不了的 **7 条**
——⛔ **剩下 8 条两边都没有**，而那句尾巴印的是「未覆盖 7 条」。

⭐ 那 8 条里，查下来是三种完全不同的东西：

| | 条款 | 实际 |
|---|---|---|
| **真洞** | B-3a 不得改依赖清单 | ⛔ 保护它的那条被**注释掉了**，而 `pyproject.toml` 真实存在 |
| **注释得对** | C-1a 设计文档路径 | 目标 `01_设计文档/` 在本仓不存在 |
| **有人挡，没记账** | B-1 · B-2 · C-3 · C-4 · C-5 · C-6 | 判据在**代码里** |

## ⭐ 判据：三档必须**划分**全部条款

⛔ 这与修 `telemetry.gate_buckets` 用的是**同一条不变式**：
**各档相加恒等于总数**——那是唯一能抓住「有东西掉进没人统计的缝里」的判据，
⚠️ 逐条断言永远抓不到（漏登的那条压根不在任何清单里）。

⛔ 而「有人挡但没记账」**不许塞进 `unjudged`**：那几条**是能判的**，
说成「判不了」是**新的假话**，只是把账做平了而已。

## ⛔ 第二轮（对抗审计）又查出三个洞

1. **同族豁免把漏登的放行了。** `fam("C-1a") == "C-1"`，而 C-1b 登记在
   `[[unjudged]]`——于是 **C-1a 借兄弟的名额过关**。实测三档相加 = **17**，
   散文 **18**，⛔ 而这个测试是**绿的**。
   ⚠️ 更要命的是 C-1a 散文里标着 `[judge: auto]`（**自称有机械判据**）。

2. **`by` 判的是字数**（`len(by) > 8`）。⛔ 9 个字的废话能过，
   **指向根本不存在的代码也能过**。哪天那段守卫被重构掉，那行字纹丝不动地
   继续声称有人挡——⚠️ 正是 rules-digest 那次教训的复刻。

3. **`[[protected_ref]]` 是静默后门**：本测试把它算进「有机械判据」，
   ⛔ 而 `load()` 从不解析它。往 toml 里加一条就能让账凭空做平。
   （该洞已在 `load()` 侧堵死：不解析的 `protected_*` 一律当场炸。）
"""

from __future__ import annotations

import importlib
import pathlib
import re
import subprocess
import tempfile
import tomllib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MD = _ROOT / ".devloop" / "constitution.md"
_TOML = _ROOT / ".devloop" / "constitution.toml"


def _prose() -> dict[str, str]:
    """散文版里逐条声明的 `条款号 → judge 标注`。⚠️ 那份才是权威。"""
    out = {}
    for m in re.finditer(r"^### ([A-Z]-[0-9a-z]+) ·(.*)$",
                         _MD.read_text(encoding="utf-8"), re.M):
        j = re.search(r"\[judge:\s*([a-z ·]+)\]", m.group(2))
        out[m.group(1)] = (j.group(1).strip() if j else "")
    return out


def _buckets() -> dict[str, set[str]]:
    d = tomllib.loads(_TOML.read_text(encoding="utf-8"))
    #  ⛔ 只数 `load()` **真正解析**的那两个键。⚠️ 早先这里还数了
    #     `protected_ref`——一个 `load()` 从不解析的名字，于是它成了
    #     「让账凭空做平」的后门。⭐ 这份清单必须与 `load()` 一致。
    judged = {x["clause"] for k in ("protected_file", "protected_tree")
              for x in d.get(k, [])}
    return {
        "有机械判据": judged,
        "判不了": {x["clause"] for x in d.get("unjudged", [])},
        "由代码挡": {x["clause"] for x in d.get("judged_elsewhere", [])},
    }


def test_每一条都必须逐条落进三档之一() -> None:
    """⛔ 本文件的核心不变式，且**不许有同族豁免**。

    ⚠️ 早先 `fam()` 把 `C-1a` 归到 `C-1`，只要 `C-1b` 登记了就放行 C-1a。
    ⭐ 但子条款是**独立的判据**：C-1a 是「设计文档**路径**变更」，
    C-1b 是别的事。一个登记了不代表另一个有人管。

    实测后果：三档相加 = 17，散文 18，⛔ 而这条测试是绿的。
    """
    prose = _prose()
    assert prose, "⛔ 一条都解析不出来，判据是空的"
    b = _buckets()
    covered = b["有机械判据"] | b["判不了"] | b["由代码挡"]
    missing = sorted(c for c in prose if c not in covered)
    assert not missing, (
        f"⛔ 这几条在 constitution.toml 里**一个清单都没进**：{missing}\n"
        f"   ⚠️ 于是「未覆盖 N 条」那句尾巴少报了。\n"
        f"   ⭐ 三个去处：有判据 → `[[protected_*]]`；真判不了 → `[[unjudged]]`；\n"
        f"      判据在代码里 → `[[judged_elsewhere]]`（必须写明谁在挡）。\n"
        f"   ⛔ 子条款（C-1a / B-3b …）要**各自**登记，不许靠同族兄弟顶名额。")


def test_三档相加必须等于散文总条数() -> None:
    """⭐ 与上一条互补：那条查「谁没进」，这条查**总数对不对得上**。

    ⚠️ 两条都要——上一条被「toml 里多登记了散文没有的条款」蒙混时仍会绿，
    而那同样让「未覆盖 N 条」那句话失真。
    """
    prose = set(_prose())
    b = _buckets()
    covered = b["有机械判据"] | b["判不了"] | b["由代码挡"]
    extra = sorted(covered - prose)
    assert not extra, (
        f"⛔ toml 里登记了散文里没有的条款：{extra}\n"
        f"   ⚠️ 散文才是权威。要么补进散文，要么从 toml 里去掉。")
    assert len(covered) == len(prose), \
        f"⛔ 三档相加 {len(covered)} ≠ 散文 {len(prose)}"


def test_散文里标auto的条款必须真有判据() -> None:
    """⭐ 这条单独就能抓住 C-1a。

    ⚠️ `[judge: auto]` 是一句**承诺**：这条有机械判据。
    ⛔ 那它就不许躺在 `[[unjudged]]`（判不了）里——两句话直接矛盾。
    """
    b = _buckets()
    has_judge = b["有机械判据"] | b["由代码挡"]
    bad = sorted(c for c, j in _prose().items()
                 if j.startswith("auto") and c not in has_judge)
    assert not bad, (
        f"⛔ 这几条散文里标着 `[judge: auto]`（自称有机械判据），"
        f"却既不在 [[protected_*]] 也不在 [[judged_elsewhere]]：{bad}\n"
        f"   ⚠️ 两句话直接矛盾。要么补上判据，要么把散文的标注改成实话。")


def _resolves(ref: str) -> bool:
    """引用指向的东西真的存在吗。⚠️ 文件走盘、点号路径走 importlib。"""
    if ref.endswith(".py"):
        return (_ROOT / ref).is_file()
    mod, *attrs = ref.split(".")
    obj = None
    for name in (f"devloop.{mod}", mod):
        try:
            obj = importlib.import_module(name)
            break
        except ImportError:
            continue
    if obj is None:
        return False
    for a in attrs:
        obj = getattr(obj, a, None)
        if obj is None:
            return False
    return True


def test_由代码挡的每一条都要指向真实存在的东西() -> None:
    """⛔ **判据从「字数」换成「引用能不能解析」**（2026-08-03）。

    早先判的是 `len(by) > 8`——⚠️ 9 个字的废话能过，
    **指向根本不存在的代码也能过**。

    后果：某天 C-4 的守卫（`worktree.commit_result` 里的 `BranchHijack` 断言）
    被重构掉或改名，`by` 那行字纹丝不动地继续声称「提交前硬断言 HEAD == …」。
    测试全绿，宪法结论里 C-4 继续算在「有人挡」那一档，**而实际上没人挡**。
    ⭐ 那正是 rules-digest 那次教训的复刻：散文写着规矩，执行它的东西没了。
    """
    d = tomllib.loads(_TOML.read_text(encoding="utf-8"))
    entries = d.get("judged_elsewhere", [])
    assert entries, "⛔ 第三档是空的"

    #  ⚠️ `模块.属性` 那一支必须排掉**文件扩展名**：`by` 是散文，里面会自然
    #     出现 `gates.sh`、`config.toml` 这类词。⛔ 第一版没排，于是这条判据
    #     自己误报了一次——**守卫过宽和过窄一样坏**。
    token = re.compile(r"(tests/[\w/.\-]+\.py|devloop/[\w/.\-]+\.py"
                       r"|[a-z_]+\.(?!sh\b|md\b|toml\b|json\b|jsonl\b|txt\b|py\b)"
                       r"[A-Za-z_][\w.]*)")
    for e in entries:
        by = e.get("by", "")
        refs = [t.rstrip(".") for t in token.findall(by)]
        assert refs, (
            f"⛔ {e.get('clause')} 的 by 里一个可核实的引用都没有：{by!r}\n"
            f"   ⭐ 至少写出一个 `tests/x.py`、`devloop/x.py` "
            f"或 `模块.函数` 形式的引用。")
        bad = [r for r in refs if not _resolves(r)]
        assert not bad, (
            f"⛔ {e.get('clause')} 的 by 指向的东西**不存在**：{bad}\n"
            f"   ⚠️ 那行字会继续声称有人挡，而守卫早就没了。\n"
            f"   完整原文：{by}")


def test_依赖清单必须被保护() -> None:
    """⛔ 这是那 8 条里**唯一的真洞**：`pyproject.toml` 真实存在，
    而保护它的那条曾被注释掉。

    ⚠️ 工人在 worktree 里加一个依赖、或改 `requires-python`，
    宪法一声不吭——而那会改变整个项目的运行环境。
    """
    assert (_ROOT / "pyproject.toml").exists(), "夹具前提变了"
    d = tomllib.loads(_TOML.read_text(encoding="utf-8"))
    globs = [t["glob"] for t in d.get("protected_tree", []) if t.get("glob")]
    assert any("pyproject" in g for g in globs), \
        f"⛔ 依赖清单没被保护：{globs}"


def test_受保护的模式必须匹配到被git跟踪的文件() -> None:
    """⚠️ 宪法自己拒绝匹配 0 个文件的模式，理由是「写错的模式会**永远报
    无命中**」。

    ⛔ **判据用生产判据 `_match` + `git ls-files`**，不用 `pathlib.Path.glob`：

    | 问题 | 后果 |
    |---|---|
    | `Path.glob` 与 `_match` 是两套语义 | 一边宽一边窄，测试说绿而生产漏 |
    | 盘上有 ≠ `check_tree` 看得见 | 它从 `git ls-tree <base>` 取清单 |
    | 合法的 `path=` 写法 | `t["glob"]` 直接 KeyError |

    ⭐ 换成生产判据，一句话同时修掉三个。
    """
    from devloop.constitution import _match

    d = tomllib.loads(_TOML.read_text(encoding="utf-8"))
    tracked = [x.strip() for x in subprocess.run(
        ["git", "-c", "core.quotepath=off", "ls-files"], cwd=_ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout.splitlines() if x.strip()]
    assert tracked, "夹具前提变了：git ls-files 是空的"

    for t in d.get("protected_tree", []):
        if t.get("allow_empty"):
            continue
        pat = t.get("glob") or t.get("path", "")
        assert pat, f"⛔ 这条既没有 glob 也没有 path：{t}"
        assert any(_match(rel, pat) for rel in tracked), \
            f"⛔ 受保护模式 `{pat}` 匹配不到任何被 git 跟踪的文件——它会永远报无命中"


def test_不解析的清单一律当场炸(tmp_path) -> None:
    """⛔ `[[protected_ref]]` 曾是**静默后门**：覆盖率统计把它算进
    「有机械判据」，而 `load()` 从不解析它——加一条就能让账凭空做平、
    尾巴凭空变短，而判据一行都没多。
    """
    from devloop import constitution as C
    from devloop.config import ConfigError, ProjectPaths

    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("exit 0\n", encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(
        'schema = 1\n\n'
        '[[protected_file]]\nclause = "A-1"\ntitle = "x"\n'
        'path = ".devloop/gates.sh"\n\n'
        '[[protected_ref]]\nclause = "A-2"\ntitle = "凭空做平"\n\n'
        '[[unjudged]]\nclause = "X-9"\nwhy = "判不了"\n\n'
        '[tree]\ncoverage = "none"\nwhy = "夹具"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        C.load(ProjectPaths(p))
    assert "protected_ref" in str(e.value), str(e.value)


def test_宪法命中必须看起来像宪法命中() -> None:
    """⛔ `BranchHijack` 曾被吞成一条普通失败。

    ⚠️ 它不是「这一单干砸了」——它是**宪法 C-4 命中**：有人把 HEAD 挪到了
    隔离分支之外，而工具差一点就要往那里提交。而原来的输出是

        ✗ 某单: BranchHijack: ...

    与任何别的异常长得**一模一样**。于是在滚动输出里它只是一条失败单，
    自动驾驶按 retries 继续重试，⛔ **人不会意识到红线被碰了**。

    ⭐ 措辞就是判据的一部分。判据落在 AST 上：那个兜底 except 里必须有一支
    专门认 `BranchHijack`。
    """
    import ast
    import inspect

    from devloop import cli

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cli)))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_unit")
    named = [n for n in ast.walk(fn)
             if isinstance(n, ast.Attribute) and n.attr == "BranchHijack"]
    assert named, (
        "⛔ `_run_unit` 的异常处理里没有一处单独认 BranchHijack——"
        "宪法命中会和普通失败长得一样，重试会一轮轮撞同一条红线。")
