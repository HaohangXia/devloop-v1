"""⛔ 工位拆不掉时，**必须喊出来**。

## 这条是怎么来的（2026-08-22，真实事故）

2026-08-21 用户的 C 盘被撑爆：

| | |
|---|---|
| `%TEMP%` 下没拆掉的 git 工位 | **2,983 个 ≈ 26 GB** |
| pytest 临时目录 | **60 GB / 一千万个以上文件** |
| 实测泄漏速率 | ⭐ **≈63 个/小时** |

**根因**：两处清理都写成
`subprocess.run([...], capture_output=True)` —— ⛔ **不查返回码。**

- `worktree.py::Worktree.remove`
- `gates.py::run_on_commit` 的 `finally`

⚠️ 而后者的注释写着「Windows 上文件可能被占用，**清理失败不应掩盖闸的结论**」。
⭐ 前半句对（不该抛异常），⛔ **后半句被执行成了「一个字都不说」**。

> ## ⭐ 不掩盖结论 ≠ 不出声。

## ⭐⭐ 它属于一个可推导的物种 —— latch T5（2026-08-22 立）

> **任何可能失败的操作，若其失败不改变任何可观测输出，**
> **则该失败必然被累积到灾难规模。**
> ⛔ 这不取决于失败率高低，只取决于**速率是否 > 0**。

```
失败无输出 → 无人知晓 → 无人修 → 累积 → 速率>0 ⇒ 必然到达灾难阈值
```

⚠️ 全仓扫描（2026-08-22）：`capture_output=True` 之后 8 行内不查
`returncode`/`stdout`/`stderr` 的共 **10 处**，⭐ 其中 **3 处**在 `worktree.py`
—— **正是撑爆磁盘的那个文件**。⇒ **T5 本可以在事发前抓到它。**

## ⛔ 为什么判据必须是「红检」

⚠️ 正常路径（删得掉）本来就不会喊。⭐ 只测正常路径 = 只做过绿检，
⛔ 证明不了「删不掉时会喊」—— 而那正是要守的行为。
⇒ **必须真造一个删不掉的场景。**
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from devloop import worktree as wt_mod


def _git(cwd: Path, *a: str) -> str:
    p = subprocess.run(["git", *a], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    assert p.returncode == 0, f"git {' '.join(a)} 失败：{p.stderr}"
    return p.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def _wt(project: Path, path: Path, branch: str) -> wt_mod.Worktree:
    """⚠️ 不走 `create()`（那要建真工位），只要 remove() 需要的三个字段。"""
    w = wt_mod.Worktree.__new__(wt_mod.Worktree)
    w.project = project
    w.path = path
    w.branch = branch
    return w


def test_拆不掉时必须喊出来(tmp_path: Path, capsys, monkeypatch) -> None:
    """⭐⭐ 红检：造一个删不掉的场景，⛔ 静默就是失败。

    ⚠️ 造法：指向一个**根本不是工位**的路径 ——
    `git worktree remove` 必然非零退出，与 Windows 文件占用同一条码路。
    """
    monkeypatch.setattr(wt_mod, "LEAKS", [])
    r = _repo(tmp_path)
    _wt(r, tmp_path / "根本不存在的工位", "devloop/不存在").remove()

    err = capsys.readouterr().err
    assert "没拆干净" in err, (
        "⛔ 拆不掉却一个字都没说 —— 这正是撑爆 C 盘的那个形状。\n"
        f"   实际 stderr：{err!r}")
    assert str(tmp_path / "根本不存在的工位") in err, "⛔ 没说是哪一个拆不掉"
    assert "worktree prune" in err, "⛔ 没告诉人怎么手工清"


def test_拆不掉要记数(tmp_path: Path, monkeypatch) -> None:
    """⛔ 只喊一声不够 —— 灾难来自**累积**，所以必须数得出来。

    ⚠️ T5 的判据落在速率上：一次泄漏无害，⭐ 63 个/小时会撑爆磁盘。
    ⇒ 人需要看见的是**累计数**，不是单次事件。
    """
    monkeypatch.setattr(wt_mod, "LEAKS", [])
    r = _repo(tmp_path)
    for i in range(3):
        _wt(r, tmp_path / f"x{i}", f"devloop/x{i}").remove()
    assert len(wt_mod.LEAKS) == 3, (
        f"⛔ 漏了 3 个只记下 {len(wt_mod.LEAKS)} 个 —— 数不准就看不出速率")


def test_拆得掉时不许瞎喊(tmp_path: Path, capsys, monkeypatch) -> None:
    """⭐ 绿检。⛔ 缺了它，上面两条可以靠「一律喊」蒙混过关 ——
    那就成了一根天天响的火警，⚠️ 而**假红比假绿贵**。
    """
    monkeypatch.setattr(wt_mod, "LEAKS", [])
    r = _repo(tmp_path)
    wt_path = tmp_path / "真工位"
    _git(r, "worktree", "add", "-q", "--detach", str(wt_path))

    w = _wt(r, wt_path, "")           # ⚠️ detached，没有分支要删
    subprocess.run(["git", "worktree", "remove", "--force", str(wt_path)],
                   cwd=r, capture_output=True)   # 先真删掉
    _git(r, "worktree", "prune")

    #  ⭐ 真正的绿检：删得掉的那次不许出声
    wt2 = tmp_path / "真工位2"
    _git(r, "worktree", "add", "-q", "-b", "devloop/ok", str(wt2))
    capsys.readouterr()                          # 清掉前面的噪音
    _wt(r, wt2, "devloop/ok").remove()

    err = capsys.readouterr().err
    assert "没拆干净" not in err, f"⛔ 删得掉却喊了 —— 假红。stderr：{err!r}"
    assert wt_mod.LEAKS == [], "⛔ 删得掉却记了一笔泄漏"
    assert not wt2.exists(), "⛔ 说是删掉了，目录还在"


def test_闸的一次性工位那条路也不许静默() -> None:
    """⛔ 判据落在**另一条路**上：`gates.py::run_on_commit` 的 `finally`。

    ⚠️ 它与 `Worktree.remove` 是**两条独立的清理路径** ——
    ⭐ 修好一条不代表另一条也修了。**同一件事有两条路，修好的永远是有人盯着的那条。**
    """
    import inspect

    from devloop import gates

    src = inspect.getsource(gates.run_on_commit)
    assert "returncode" in src, (
        "⛔ 闸的一次性工位清理又不查返回码了 —— "
        "那正是 2026-08-21 撑爆 C 盘的两条路之一")
    assert "LEAKS" in src, "⛔ 闸那条路没记数，看不出累积速率"
