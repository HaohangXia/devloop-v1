# 角色

你是这个 Python 项目的维护者。⛔ 只做本单点名的事，别顺手改别的。

# 任务

**`devloop/constitution.py` 里两个探针不检 `returncode`，git 一旦不可用就静默变成空集合，
而空集合会让守卫报绿。**

```python
def _refs(project: Path) -> dict[str, str]:
    r = subprocess.run(["git", "for-each-ref", ...], ...)
    out = {}
    for ln in r.stdout.splitlines():   # ⛔ git 失败时 stdout 是空的 → out 是空的
        ...
    return out
```

`_remotes` 同形（`git config --get-regexp`）。

## ⛔ 为什么这是缺陷而不是小事

`check_refs` 拿这两个函数的结果做**集合差**。两边都是空集合 → 差也是空 → **零命中**。

于是：git 不在 PATH 上、仓库损坏、被别的进程锁住 —— 任何一种，
`check_refs` 都会安静地报「无宪法命中」，而它其实**一条引用都没看到**。

⭐ 这正是本仓反复在防的第一种假绿：**守卫的目标不存在，而守卫报没事。**
⚠️ 对照 `gates.py`：闸自己坏了要归 `gate_code = 2`（判定器故障），
**绝不能**和「查过了没事」混成一档。宪法这侧的对应物是 `ConstitutionResult.code = 2`。

## 要做的

1. 让 `_refs` 与 `_remotes` 能把「git 探针失败」这件事**传出去**
   （⭐ 怎么传由你定：返回 `None`、抛一个内部异常、多返回一个标志位都行，
   ⚠️ 但必须让调用方**分得出**「没有引用」和「没查成」）。
2. `check_refs` 收到探针失败时，返回 **`code = 2`**（判定器故障），
   `stderr` 里说清是哪个探针失败、git 的原始报错是什么。
   ⛔ **不许**归 0（无命中），也不许归 1（有命中）。
3. `Snapshot` 那两个字段的语义要在注释里写清楚。

# 改动范围

- `devloop/constitution.py`
- `tests/test_git_probe_failure.py`（**新建**）

# 自己验

- [ ] `python -m pytest -q` 全绿
- [ ] 新测试**至少三条**：
      ① 探针失败 → `check_refs` 的 `code == 2`（⛔ 不是 0）
      ② 失败原因出现在 `stderr` 里（人看得出是 git 挂了，不是「没越线」）
      ③ ⭐ **正常情况不许被削弱**：git 好好的时候，原有判据全部照旧
        （新推分支仍命中 C-3、工具自己的 `devloop/*` 分支仍不命中）
- [ ] ⭐ **红检**：把你的修复退回去（让探针照旧吞掉失败），
      确认①②当场转红。把红检结果写进你的报告。

# 禁令

- ⛔ **不许修改任何已有的测试文件。** 只许新建 `tests/test_git_probe_failure.py`。
  （闸里有一道「测试守卫」专门查这个，改了就红。）
- ⛔ 不许碰 `.devloop/` 下的任何文件（闸、宪法、规则摘要都在那儿）。
- ⛔ 不许改 `pyproject.toml`。
- ⚠️ 不许为了让测试过而放宽断言。⭐ 判据要落在**能直接量的东西**上：
  `code` 的值、`stderr` 里有没有那几个字。
- ⛔ 不许 `git push`，不许碰 `master`。你的产出由工具提交到隔离分支。
