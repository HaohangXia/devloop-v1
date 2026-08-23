"""⛔ 重新锚定必须让人**看得见自己在批准什么**。

## 缺陷的形状（2026-08-03 实测）

`constitution anchor` 的全部实现曾经是三行：读、覆盖、印一句固定文案。

```python
a = constitution.write_anchor(paths, con)
print(f"已登记 {len(con.files)} 个受保护文件的指纹 → {a}")
```

⛔ 没有：锚是否已存在的判断 · 新旧指纹对比 · 二次确认 · 旧锚备份。
在已有锚之上再锚一次，输出与**首次**锚定**逐字相同**。

⚠️ 而整套设计的说法是「重新锚定必须由人亲自执行，因为**人是那道闸**」
（`cmd_constitution` 自己的注释：「锚一旦能自愈，它就不是锚」）。
⭐ **那道闸当时是蒙着眼睛的。** 用户当天就是在没看差异的情况下按的。

## ⛔ 后果链

```
工人在两次派单之间往 gates.sh 里加一行 exit 0
  → 人因为别的原因跑了一次 anchor
    → 命令印「已登记 5 个受保护文件的指纹」并返回 0
      → ⛔ 既不提 gates.sh 的指纹变了，也不提锚已存在
        → 被改过的 gates.sh 成为新基准，旧锚被覆盖且无备份
          → 此后 verify_anchor 永远返回 0，闸已空而每单都报「无宪法命中」
```

⭐ 对照：同一模块读 toml 那一侧极其严苛——schema 不认、路径不存在、
glob 匹配 0 个、未登记 unjudged，四种全部当场炸。**锚这一侧一道校验都没有。**

## 判据

⛔ 不判「函数返回了什么」——判**屏幕上有没有出现那几个字**，
因为设计依赖的是**人读到它**。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devloop import cli
from devloop import constitution as C
from devloop.config import ProjectPaths

_TOML = """schema = 1

[[protected_file]]
clause = "A-1"
title  = "不得修改宪法本身"
path   = ".devloop/constitution.toml"

[[protected_file]]
clause = "A-1"
title  = "不得修改闸"
path   = ".devloop/gates.sh"

[[unjudged]]
clause = "X-9"
why    = "判不了"

[tree]
coverage = "none"
why      = "夹具项目没有树内基线"
"""


def _proj(tmp_path) -> ProjectPaths:
    p = tmp_path / "proj"
    (p / ".devloop").mkdir(parents=True)
    (p / ".devloop" / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n",
                                             encoding="utf-8")
    (p / ".devloop" / "constitution.toml").write_text(_TOML, encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=p, check=True, capture_output=True)
    return ProjectPaths(p)


def _anchor(paths, capsys, *, force=False) -> tuple[int, str]:
    code = cli.cmd_constitution(
        type("A", (), {"action": "anchor", "project": str(paths.project),
                       "force": force})())
    return code, capsys.readouterr().out


# ── ① 有变化时必须印出来，且不许默默覆盖 ──────────────────────────

def test_重锚时必须逐条印出旧指纹到新指纹(tmp_path, capsys) -> None:
    """⭐ **本文件的核心。** 判据落在屏幕文本上。"""
    paths = _proj(tmp_path)
    _anchor(paths, capsys)                       # 首次
    (paths.project / ".devloop" / "gates.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n# 工人偷偷加的\n", encoding="utf-8")

    code, out = _anchor(paths, capsys)
    assert "gates.sh" in out, f"⛔ 没说是哪个文件变了：\n{out}"
    assert "→" in out, f"⛔ 没印出 旧→新：\n{out}"
    assert code != 0, "⛔ 有变化却直接覆盖了——那道「人的闸」还是瞎的"


def test_有变化且没给force时不许写锚(tmp_path, capsys) -> None:
    """⛔ 印出来还不够——必须**真的没写**。

    ⚠️ 「印了警告然后照样写」是这个项目分得最清的一类：`--discard` 那次
    （G-94）就是实现了但控制流够不着。判据要落在**锚文件本身变没变**上。
    """
    paths = _proj(tmp_path)
    _anchor(paths, capsys)
    before = C.load(paths).anchor_path.read_bytes()

    (paths.project / ".devloop" / "gates.sh").write_text("x\n", encoding="utf-8")
    _anchor(paths, capsys)
    assert C.load(paths).anchor_path.read_bytes() == before, \
        "⛔ 没给 --force 却把锚覆盖了"


def test_给了force才真的覆盖并留下旧锚备份(tmp_path, capsys) -> None:
    paths = _proj(tmp_path)
    _anchor(paths, capsys)
    old = C.load(paths).anchor_path.read_bytes()

    (paths.project / ".devloop" / "gates.sh").write_text("x\n", encoding="utf-8")
    code, out = _anchor(paths, capsys, force=True)
    con = C.load(paths)
    assert code == 0, out
    assert con.anchor_path.read_bytes() != old, "⛔ 给了 --force 却没写"
    prev = con.anchor_path.with_suffix(con.anchor_path.suffix + ".prev")
    assert prev.exists() and prev.read_bytes() == old, \
        "⛔ 旧锚没留备份——「上一次的基准是什么」事后查不了"


def test_没有变化时明说无需重锚且不写盘(tmp_path, capsys) -> None:
    """⚠️ 无谓的摩擦会让人养成一律带 --force 的习惯——那等于没有闸。"""
    paths = _proj(tmp_path)
    _anchor(paths, capsys)
    before = C.load(paths).anchor_path.read_bytes()
    code, out = _anchor(paths, capsys)
    assert code == 0 and "完全一致" in out, out
    assert C.load(paths).anchor_path.read_bytes() == before


def test_首次锚定不该有额外摩擦(tmp_path, capsys) -> None:
    paths = _proj(tmp_path)
    code, out = _anchor(paths, capsys)
    assert code == 0, f"⛔ 首次锚定就要 --force：\n{out}"
    assert C.load(paths).anchor_path.exists()


# ── ② 锚自己坏了，必须说清是哪种坏 ────────────────────────────────

def test_锚不是合法JSON时要说明是锚坏了(tmp_path) -> None:
    """⛔ 原来这里直接让 `json.loads` 往上抛，错误文本是
    「Unterminated string starting at: line 4 column 22」——
    ⚠️ 既不提锚路径、也不提这是锚坏了，人会往任务书和 toml 上排查。
    """
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    con.anchor_path.write_text('{"schema": 1, "files": {"a"', encoding="utf-8")

    r = C.verify_anchor(paths, con)
    assert r.broken, "⛔ 锚坏了却没归 code 2"
    assert "锚" in r.stderr and str(con.anchor_path) in r.stderr, r.stderr
    assert "中断" in r.stderr, f"⛔ 没提示这多半是写到一半：{r.stderr}"


def test_锚缺constitution_sha时不许静默跳过自指判据(tmp_path) -> None:
    """⛔ **这条最要紧。** 原代码是 `if rec.get("constitution_sha") and ...`
    ——缺字段就**短路跳过 A-1**，而 summary() 照常印「无宪法命中」。

    ⚠️ 于是：锚缺了那个字段 + 把 constitution.toml 从受保护清单里去掉，
    宪法本身可被任意修改而**永远零命中**。
    """
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    rec = json.loads(con.anchor_path.read_text(encoding="utf-8"))
    del rec["constitution_sha"]
    con.anchor_path.write_text(json.dumps(rec), encoding="utf-8")

    r = C.verify_anchor(paths, con)
    assert r.broken, f"⛔ 锚缺字段却照常判定：{r.summary()}"
    assert "constitution_sha" in r.stderr, r.stderr


def test_锚的schema比工具新时拒绝判定(tmp_path) -> None:
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    rec = json.loads(con.anchor_path.read_text(encoding="utf-8"))
    rec["schema"] = C.ANCHOR_SCHEMA + 1
    con.anchor_path.write_text(json.dumps(rec), encoding="utf-8")
    assert C.verify_anchor(paths, con).broken


def test_写锚是原子的(tmp_path) -> None:
    """⭐ 先写临时文件再改名——从源头消掉「写到一半」这个状态。
    ⚠️ 判据落在**没有残留临时文件**上；截断态本身不好在测试里造。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    a = C.write_anchor(paths, con)
    leftovers = list(a.parent.glob("*.tmp"))
    assert not leftovers, f"⛔ 残留临时文件：{leftovers}"
    assert json.loads(a.read_text(encoding="utf-8"))["schema"] == C.ANCHOR_SCHEMA


# ── ③ 差异计算本身 ────────────────────────────────────────────────

def test_差异要认出新增与消失两种(tmp_path) -> None:
    """⚠️ 「文件被删了」和「文件被改了」对人是两件事，不许混成一句。"""
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    (paths.project / ".devloop" / "gates.sh").unlink()

    delta = C.anchor_delta(paths, con)
    hit = [d for d in delta if "gates.sh" in d[0]]
    assert hit, f"⛔ 文件没了却没进差异：{delta}"
    assert hit[0][1] and not hit[0][2], f"⛔ 新值该是空（不存在）：{hit[0]}"


def test_锚不存在时差异等于全部新增(tmp_path) -> None:
    paths = _proj(tmp_path)
    con = C.load(paths)
    delta = C.anchor_delta(paths, con)
    assert len(delta) >= 2, delta
    assert all(old == "" for _, old, _ in delta), delta


# ── ④ G-97：行尾不该动摇指纹 ─────────────────────────────────────

def test_同一份内容换个行尾指纹必须不变(tmp_path) -> None:
    """⛔ **这条是 G-97 的正面判据。**

    2026-08-03 在真仓实地撞到：改了 `.devloop/rules-digest.md` 一行，
    再 `git checkout --` 撤销 → git 重写它、autocrlf 写成 CRLF →
    指纹 `52a4e7a65dd2 → 16bf911827d6` → ⛔ A-1 命中「受保护文件与锚不符」，
    而**内容一个字都没变**。

    ⚠️ 而人对误报的自然反应是「重锚一下就好」——那正是设计禁止的自愈。
    """
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    body = "第一行\n第二行\n第三行\n"
    lf.write_bytes(body.encode())
    crlf.write_bytes(body.replace("\n", "\r\n").encode())

    assert lf.read_bytes() != crlf.read_bytes(), "夹具前提变了"
    assert C._sha(lf) == C._sha(crlf), \
        "⛔ 行尾一变指纹就变——一次 `git checkout --` 就能伪造出「宪法被改」"


def test_内容真变了指纹必须变(tmp_path) -> None:
    """⚠️ 归一化不许把**真篡改**也一起抹平。

    ⛔ 只判上一条会被「`_sha` 直接返回常数」通过——那是判据维度错。
    """
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"exit 1\n")
    b.write_bytes(b"exit 0\n")            # 工人把闸改绿了
    assert C._sha(a) != C._sha(b)
    #  ⚠️ 只差一个空格也要抓到
    c = tmp_path / "c.txt"
    c.write_bytes(b"exit  1\n")
    assert C._sha(a) != C._sha(c)


def test_指纹可以从git内容推导出来(tmp_path) -> None:
    """⭐ G-97 附带修好的第二件事：**审计路径**。

    ⚠️ 归一之前，锚里的指纹对应不上任何一个提交——而「这文件被改成什么了」
    正是人拿到 A-1 告警后要做的第一个动作。
    """
    paths = _proj(tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=paths.project, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=paths.project,
                   check=True, capture_output=True)

    import hashlib
    blob = subprocess.run(
        ["git", "cat-file", "blob", "HEAD:.devloop/gates.sh"],
        cwd=paths.project, capture_output=True).stdout
    from_git = hashlib.sha256(blob.replace(b"\r\n", b"\n")).hexdigest()
    assert C._sha(paths.project / ".devloop" / "gates.sh") == from_git, \
        "⛔ 锚里的指纹推不回 git 内容——审计动作做不了"


# ── ⑤ 故障消息不许把人要敲的命令砍成半句 ──────────────────────────

def test_自救命令不许被截断(tmp_path) -> None:
    """⛔ **2026-08-03 实测：工具停着，而它印的唯一一条自救命令敲不通。**

    屏幕上是：

        python -m devloop.cli constitution anchor --pro

    句子在这里断了——`self.stderr.strip()[:200]`，而项目路径正好在第 201 个字之后。

    ⚠️ 同一个毛病 `nightly.py` 也犯过（砍 100 字），两次都恰好砍在
    「你该干什么」那一句上。⭐ 这不是巧合：**那句话总在最后。**
    """
    paths = _proj(tmp_path)
    con = C.load(paths)
    C.write_anchor(paths, con)
    #  造一个旧口径的锚——⭐ 这正是用户当前撞到的那一种
    rec = json.loads(con.anchor_path.read_text(encoding="utf-8"))
    rec["schema"] = 0
    con.anchor_path.write_text(json.dumps(rec, ensure_ascii=False),
                               encoding="utf-8")

    s = C.verify_anchor(paths, con).summary()
    assert "constitution anchor --project" in s, \
        f"⛔ 自救命令被砍了：\n{s}"
    assert str(paths.project) in s, \
        f"⛔ 命令里的项目路径被砍了，人照抄敲不通：\n{s}"


def test_截断本身还在只是不许断在行中间() -> None:
    """⚠️ 光把上限抬高不算修好——⛔ 那只是把同一个坑往后挪。

    ⭐ 判据落在**规则**上：截断点若落在某一行中间，就把那整行留全。
    """
    long = "\n".join(f"第 {i} 行：" + "填充" * 30 for i in range(40))
    out = C._keep_命令(long, cap=100)
    assert len(out) < len(long), "⛔ 根本没截断"
    #  ⭐ 关键：留下来的部分，最后一个完整行不许是半句
    body = out.split("\n   …")[0]
    assert body.splitlines()[-1] in long.splitlines(), \
        f"⛔ 最后一行被砍成了半句：{body.splitlines()[-1]!r}"


def test_短消息一个字都不许动() -> None:
    """⚠️ 截断逻辑不许顺手改写正常消息。"""
    assert C._keep_命令("就一句话") == "就一句话"


# ── ⑥ 差异表不许对人说假话（2026-08-03 用户实测撞到）────────────────

def _make_old_anchor(paths, *, schema=0):
    """造一份**旧口径**的锚：文件都在，只是指纹用老算法算的。"""
    con = C.load(paths)
    C.write_anchor(paths, con)
    rec = json.loads(con.anchor_path.read_text(encoding="utf-8"))
    rec["schema"] = schema
    #  ⚠️ 旧口径 = 不归一行尾。这里把其中一个的值改成「原样字节」的哈希，
    #     另一个保持不变（纯 LF 文件两种算法结果相同）——⭐ 真实情况就是混的。
    import hashlib
    g = paths.project / ".devloop" / "gates.sh"
    rec["files"][".devloop/gates.sh"] = hashlib.sha256(
        g.read_bytes() + b"\r\n").hexdigest()      # 假装是 CRLF 版
    con.anchor_path.write_text(json.dumps(rec, ensure_ascii=False),
                               encoding="utf-8")
    return con, rec


def test_旧锚里的文件不许被印成当时不存在(tmp_path) -> None:
    """⛔ **用户 2026-08-03 真撞到的那一幕。**

    他跑重锚，屏幕上列出 5 个文件，每一个都写着「（当时不存在）→ 新指纹」。
    ⚠️ 而那 5 个**全都在旧锚里**，白纸黑字。

    原因：`anchor_delta` 用了 `read_anchor`，而后者对旧口径的锚返回 `None`
    （判定路径上那是对的）→ `old_files` 变成空 → 全部看起来像新增。

    ⭐ **那张差异表存在的唯一理由，就是让人看清自己在批准什么。**
    """
    paths = _proj(tmp_path)
    con, rec = _make_old_anchor(paths)

    delta = C.anchor_delta(paths, con)
    for name, old, new in delta:
        assert old, (
            f"⛔ `{name}` 被印成「（当时不存在）」，而它就在锚里：\n"
            f"   锚里记的是 {rec['files'].get(name, '（这一条确实不在）')}\n"
            f"   ⚠️ 差异表在人按下批准的那一刻对他说了假话。")


def test_新旧指纹一样的文件不许出现在差异表里(tmp_path) -> None:
    """⛔ 用户那次，`rules-digest.md` 新旧指纹**完全一样**（`52a4e7a65dd2`），
    却被列进了「你要批准的东西」。

    ⚠️ 差异表里混进没变的东西 = 让人在噪音里找信号，
    ⭐ 而这张表的全部价值就是**短**。
    """
    paths = _proj(tmp_path)
    con, rec = _make_old_anchor(paths)
    delta = C.anchor_delta(paths, con)
    same = [(n, o, w) for n, o, w in delta if o == w]
    assert not same, f"⛔ 这几条新旧一样却被列出来了：{same}"


def test_登记时间读得出来就不许说读不出来(tmp_path) -> None:
    """⚠️ 那个时间戳是**普通 JSON 字段**，一直好好地在文件里。
    ⛔ 它印成「（读不出来）」，只因为判定层拒绝了这份锚——**两件事**。"""
    paths = _proj(tmp_path)
    con, rec = _make_old_anchor(paths)
    peek = C.peek_anchor(con)
    assert peek.get("at") == rec["at"], \
        f"⛔ 时间戳读不出来：{peek.get('at')!r} vs 锚里的 {rec['at']!r}"


def test_尽力读绝不许被判定路径用到() -> None:
    """⛔ **这条是防线。** `peek_anchor` 是宽松的（坏锚也尽力读），
    ⚠️ 它一旦漏进判定路径，「锚坏了拒绝判定」那条规矩就没了。

    ⭐ 判据落在 AST 上：`verify_anchor` 里不许出现 `peek_anchor`。
    """
    import ast
    import inspect

    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(C)))
              if isinstance(n, ast.FunctionDef) and n.name == "verify_anchor")
    bad = [n for n in ast.walk(fn)
           if isinstance(n, ast.Name) and n.id == "peek_anchor"]
    assert not bad, \
        "⛔ verify_anchor 用上了 peek_anchor——判定路径被宽松读法污染了"


def test_同一个文件不许在差异表里出现两遍(tmp_path) -> None:
    """⚠️ 用户那次看到 `constitution.toml（宪法自身）` 与
    `.devloop/constitution.toml` **并排出现**，看起来像两处改动，其实是一个文件。"""
    paths = _proj(tmp_path)
    con, _ = _make_old_anchor(paths)
    delta = C.anchor_delta(paths, con)
    hashes = [new for _, _, new in delta]
    dup = {h for h in hashes if h and hashes.count(h) > 1}
    assert not dup, (
        f"⛔ 同一个文件在差异表里出现了不止一次（指纹 {dup}）：\n"
        f"   {[d[0] for d in delta]}")
