#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DevLoop 对账报告 · 无转义分隔块格式（G-33 的修法）。

**为什么换掉 JSON**：两批 20 份报告里 4 份末尾 JSON 解析失败，三种撞法全是
「自由文本进 JSON 字符串」——`grep 'a\\|b'` 的 `\\|` 是非法转义、中文里夹 ASCII
双引号。内容全都是好的，只差转义。**不是模型能力问题，是格式本身有雷。**

**本格式的核心保证（不是「概率上不撞」，是「结构上撞不到」）**：

    结构行 = 行首两个字符为 `@@` 的行。
    值只出现在两个位置：① `键: ` 之后（前面必有键名，行首不可能是 `@@`）
                        ② `+ ` 续行之后（行首是 `+`，不可能是 `@@`）
    ⇒ 值无论写什么内容，都生不出一个结构行。分隔符不需要转义，也不需要「够罕见」。

实测佐证：`@@` 在 eco-ob-p4 全仓 210 文件 40,958 行里行首出现 **0** 次，
在 23 份报告 + 18 份任务书里 **0** 次；同口径下 ``` 行首出现 92 + 76 次
（所以 markdown 围栏当分隔符是错的——工人引用一段带围栏的文档就会把块劈断）。

**分层纪律**（对齐「不许静默降级」）：
  - 传输层（parse）：能不能把字节切成 键→值。切不动就是**失败**，不猜。
  - 模式层（validate）：字段缺没缺、认不认识。**只出告警，不改判**——因为今天的
    JSON 路径对缺字段也是宽容的（u08 有一条 not_finding 缺 `doc_quote`，照样进统计），
    在这里改成硬失败等于把本来能用的报告判死，违反「不许砸掉现在能用的东西」。
    要严格模式的调用方自己传 `strict=True`。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as _f

# ── 词汇表 ──────────────────────────────────────────────────────────
BEGIN = "@@DEVLOOP v1"
END = "@@END"
ITEM = "@@ITEM"
CONT = "+"                       # 续行前缀（`+ ` 或单独一个 `+` 表示空行）

# 块内允许出现、但不承载信息的行（工人爱给块套围栏，容忍但要记账）
_FENCE = re.compile(r"^\s*(?:```|~~~)[A-Za-z0-9_-]*\s*$")
_FIELD = re.compile(r"^([a-z][a-z0-9_]{0,31}):(?: ?(.*))?$")
# 块首允许**整块统一缩进**（工人把块写进 markdown 列表/引用里是常见排版）。
# 缩进量从块首行量出来，块内每行去掉同样长度的前缀 —— 见 slice_block。
# ⚠️ 这不削弱「值生不出结构行」的保证：能被认成块首的行，前缀只能是空白；
#    而值只出现在 `键: ` 之后和 `+ ` 之后，那两种行的行首永远是键名首字母或 `+`。
_BEGIN = re.compile(r"^([ \t]*)@@DEVLOOP(?:\s+v(\d+))?\s*$")

# kind → 旧 JSON 里的分节名。别名只收「单复数」两种写法，不再扩。
KIND_ALIAS = {
    "meta": "meta", "metas": "meta",
    "finding": "findings", "findings": "findings",
    "undecidable": "undecidable", "undecidables": "undecidable",
    "not_finding": "not_findings", "not_findings": "not_findings",
    "variants": "variants", "variant": "variants",
    "self_check": "self_check", "selfcheck": "self_check",
}

# 每类的字段：(必需, 可选)。顺序即 emit 顺序。
SCHEMA: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "meta":         (("unit", "base"), ()),
    "findings":     (("doc_anchor", "doc_quote", "verify", "expect",
                      "evidence", "why", "confidence"), ()),
    "undecidable":  (("doc_anchor", "doc_quote", "reason"), ()),
    "not_findings": (("doc_anchor", "doc_quote", "checked", "verdict"), ()),
    "variants":     ((), ("tried",)),           # tried 可重复，是唯一的列表字段
    # self_check 全部可选：它**不参与判分**（判定看产物不看自评），
    # 为一个不参与判分的东西出「缺字段」告警只会制造噪声。
    # `json_valid` 是旧名，块格式下叫 `block_closed`，两个都收。
    "self_check":   ((), ("no_line_anchors", "path_scoped_grep",
                          "block_closed", "json_valid")),
}
MULTI = {("variants", "tried")}                  # 允许重复出现的 (kind, key)
LIST_SECTIONS = ("findings", "undecidable", "not_findings")

_TRUE = {"yes", "true", "y", "1", "是", "✅"}
_FALSE = {"no", "false", "n", "0", "否", "❌"}


@dataclass
class ParseResult:
    """`obj` 只在 `ok` 为真时有值。**`salvage` 永远不进统计**，只给人看。"""
    obj: dict | None = None
    source: str = ""                    # block | json | repaired | ""
    errors: list[str] = _f(default_factory=list)     # 传输层：致命
    warnings: list[str] = _f(default_factory=list)   # 模式层：诊断
    salvage: list[dict] = _f(default_factory=list)   # ⛔ 诊断用，禁止计入任何指标

    @property
    def ok(self) -> bool:
        return self.obj is not None and not self.errors


# ── 一、切块 ────────────────────────────────────────────────────────
def slice_block(text: str) -> tuple[list[tuple[int, str]] | None, str]:
    """取**最后一个** `@@DEVLOOP` 到其后第一个 `@@END` 之间的行。

    取最后一个：正文里可能引用了格式说明（工人被要求照抄格式），真块永远在最末。
    找不到 END = **块不完整**（撞轮数上限被截断就是这个形态），直接判失败，
    不许拿半个块凑数。
    """
    lines = text.splitlines()
    begins = [i for i, ln in enumerate(lines) if _BEGIN.match(ln)]
    if not begins:
        return None, "没有 @@DEVLOOP 块"
    b = begins[-1]
    pad, ver = _BEGIN.match(lines[b]).groups()
    if ver not in (None, "1"):
        return None, f"块版本 v{ver} 不认识（本解析器只认 v1）"

    def unpad(s: str) -> str:
        return s[len(pad):] if pad and s.startswith(pad) else s

    for i in range(b + 1, len(lines)):
        if unpad(lines[i]).rstrip() == END:
            return [(n, unpad(s)) for n, s in
                    enumerate(lines[b + 1:i], start=b + 2)], ""
    return None, "块不完整：有 @@DEVLOOP 但没有 @@END（多半是被截断了）"


# ── 二、切词：行 → 项 ──────────────────────────────────────────────
def tokenize(numbered: list[tuple[int, str]]) -> tuple[list[dict], list[str], list[str]]:
    """把块内的行切成 items。返回 (items, errors, warnings)。

    ⚠️ **不做隐式续行**。既不是 `@@`、不是 `键: `、不是 `+ ` 的行一律报
    `孤儿行` 并给出行号原文——宁可报错让人一眼看见，也不猜它属于谁。
    """
    items: list[dict] = []
    errs: list[str] = []
    warns: list[str] = []
    cur: dict | None = None
    last_key: str | None = None

    for lineno, raw in numbered:
        ln = raw.rstrip()
        if ln.startswith("@@"):
            last_key = None
            head, _, rest = ln.partition(" ")
            if head != ITEM:
                errs.append(f"第 {lineno} 行：不认识的标记 {ln!r}")
                continue
            kind_raw = rest.strip().lower()
            kind = KIND_ALIAS.get(kind_raw)
            if kind is None:
                errs.append(f"第 {lineno} 行：不认识的条目类型 {rest.strip()!r}")
                cur = None
                continue
            # 单复数两种写法都是**文档里写明的合法拼法**，归一不算降级，不出告警——
            # 告警一旦有噪声就没人看了，那才是真的静默。
            cur = {"_kind": kind, "_line": lineno, "_fields": {}}
            items.append(cur)
            continue

        if ln.startswith(CONT) and (len(ln) == 1 or ln[1] == " "):
            if cur is None or last_key is None:
                errs.append(f"第 {lineno} 行：续行 `+` 前面没有字段")
                continue
            cur["_fields"][last_key][-1] += "\n" + ln[2:]
            continue

        if not ln.strip():
            # 空行**切断续行上下文**：空行之后再写 `+ ` 就是错误，而不是接到
            # 几行之前的那个字段上。条目之间用空行分隔因此是安全的。
            last_key = None
            continue
        if _FENCE.match(ln):
            warns.append(f"第 {lineno} 行：忽略了一行代码围栏 {ln.strip()!r}")
            continue

        m = _FIELD.match(ln)
        if m:
            if cur is None:
                errs.append(f"第 {lineno} 行：字段 {m.group(1)!r} 出现在任何 @@ITEM 之前")
                continue
            k, v = m.group(1), (m.group(2) or "")
            cur["_fields"].setdefault(k, []).append(v)
            last_key = k
            continue

        errs.append(f"第 {lineno} 行：孤儿行（既不是 @@ 标记、也不是 `键: 值`、"
                    f"也不是 `+ ` 续行）→ {ln[:80]!r}")
    return items, errs, warns


# ── 三、校验 + 装配成旧 JSON 的形状 ────────────────────────────────
def assemble(items: list[dict], strict: bool = False) -> tuple[dict, list[str], list[str]]:
    obj: dict = {"unit": None, "base": None,
                 "findings": [], "undecidable": [], "not_findings": [],
                 "naming_variants_tried": [], "self_check": {}}
    errs: list[str] = []
    warns: list[str] = []
    seen_single = set()

    for it in items:
        kind, ln, fields = it["_kind"], it["_line"], it["_fields"]
        req, opt = SCHEMA[kind]
        known = set(req) | set(opt)

        if kind in ("meta", "self_check", "variants"):
            if kind in seen_single and kind != "variants":
                warns.append(f"第 {ln} 行：{kind} 出现了不止一次，后一个覆盖前一个")
            seen_single.add(kind)

        for k in fields:
            if k not in known:
                warns.append(f"第 {ln} 行：{kind} 里多了字段 {k!r}（值已保留，不参与统计）")

        miss = [k for k in req if k not in fields]
        if miss and len(miss) * 2 > len(req):
            # 缺一半以上不是笔误，是结构塌了（最常见的成因：多行值漏写 `+ ` 前缀，
            # 那一行以 `@@ITEM` 打头，于是伪造出一个条目、把真条目的字段劈走）。
            # ⛔ 这一档必须硬失败：不然被劈出来的空壳条目会直接抬高「矛盾合计」。
            errs.append(f"第 {ln} 行：{kind} 缺了 {len(miss)}/{len(req)} 个必需字段 "
                        f"{miss} —— 条目结构不完整，不是笔误")
        else:
            for k in miss:
                warns.append(f"第 {ln} 行：{kind} 缺字段 {k!r}")

        # 字段顺序检查 —— **这是全格式唯一那条静默漏洞的检测手段，所以判死不判轻**。
        # 漏洞形态：多行值漏写 `+ ` 前缀，而续行恰好以某个已知字段名打头，
        #           于是前一个字段被截短、这个字段被顶替，两处内容都被改写。
        # 判死的依据：模板把顺序钉死了，实测 **505/505 个历史条目键顺序与模板一致，
        #           0 例外**；而被吸收的行几乎必然把顺序打乱（并附带一个重复字段）。
        # 即：这条检查的历史误杀率是 0，换来的是堵掉唯一的静默改写路径。
        seq = [k for k in fields if k in req]
        canon = [k for k in req if k in fields]
        if seq != canon:
            errs.append(f"第 {ln} 行：{kind} 的字段顺序与模板不符（{seq} vs {canon}）"
                        f" —— 多半是某个多行值漏写了 `+ ` 前缀、被下一行顶替了，"
                        f"内容可能已被改写，不敢当成功")

        for k, vs in fields.items():
            if len(vs) > 1 and (kind, k) not in MULTI:
                warns.append(f"第 {ln} 行：{kind} 的字段 {k!r} 出现了 {len(vs)} 次，"
                             f"只取第一次（其余已丢弃）")

        flat = {k: vs[0].strip() for k, vs in fields.items()}

        if kind == "meta":
            obj["unit"] = flat.get("unit") or obj["unit"]
            obj["base"] = flat.get("base") or obj["base"]
        elif kind == "variants":
            obj["naming_variants_tried"] = [v.strip() for v in fields.get("tried", [])]
        elif kind == "self_check":
            for k, v in flat.items():
                lv = v.strip().lower()
                if lv in _TRUE:
                    obj["self_check"][k] = True
                elif lv in _FALSE:
                    obj["self_check"][k] = False
                else:
                    obj["self_check"][k] = v
                    warns.append(f"第 {ln} 行：self_check.{k} = {v!r} 不是是/否，原样保留")
        else:
            obj[kind].append(flat)

    if obj["unit"] is None:
        warns.append("没有 @@ITEM meta / unit —— 汇总时会退回用文件名推断")
    if strict and warns:
        errs.extend(f"[strict] {w}" for w in warns)
    return obj, errs, warns


def parse_block(text: str, strict: bool = False) -> ParseResult:
    numbered, why = slice_block(text)
    if numbered is None:
        return ParseResult(errors=[why])
    items, errs, warns = tokenize(numbered)
    obj, errs2, warns2 = assemble(items, strict=strict)
    errs += errs2
    warns += warns2
    if errs:
        # ⛔ 失败就是失败。salvage 只是给人看「本来切出了什么」，obj 保持 None。
        return ParseResult(obj=None, source="block", errors=errs, warnings=warns,
                           salvage=[{"kind": i["_kind"], "line": i["_line"],
                                     "fields": {k: v[0] for k, v in i["_fields"].items()}}
                                    for i in items])
    return ParseResult(obj=obj, source="block", warnings=warns)


# ── 四、新旧共存：一个入口读两种格式 ──────────────────────────────
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def parse_legacy_json(text: str) -> ParseResult:
    blocks = _JSON_BLOCK.findall(text)
    if not blocks:
        return ParseResult(errors=["没有 ```json 代码块"])
    try:
        return ParseResult(obj=json.loads(blocks[-1]), source="json")
    except Exception as e:
        return ParseResult(source="json", errors=[f"JSON 解析失败：{e}"])


def load_report(receipt: dict, strict: bool = False) -> ParseResult:
    """从一份回执里取出结构化产物。**新旧两种格式都能读，历史报告不作废。**

    优先级（先命中先用，且 `source` 会记下走的哪条路）：
      1. `@@DEVLOOP` 块   —— 新格式。**排在最前**：`_repaired_json` 是给旧格式
         救场的历史字段，如果有人给一份新格式报告也加了它，真块会被整个跳过。
         实测 23 份历史报告一份都没有块，所以这次调序对它们没有任何影响。
      2. `_repaired_json` —— 已被 repair_json.py 救回的 4 份，原样沿用
      3. ```json 块       —— 旧格式（19 份历史报告走这条）
    """
    text = receipt.get("result") or ""
    # 「有没有新块」只看**独占一行**的 @@DEVLOOP（可整块缩进）——
    # 正文里带反引号提一嘴不算。
    if any(_BEGIN.match(ln) for ln in text.splitlines()):
        return parse_block(text, strict=strict)
    if receipt.get("_repaired_json"):
        return ParseResult(obj=receipt["_repaired_json"], source="repaired")
    return parse_legacy_json(text)


# ── 五、反向：旧 JSON → 块文本（迁移与自测用） ────────────────────
def to_block(obj: dict) -> str:
    """把旧 JSON 形状写成块。**多行值自动加 `+ ` 续行前缀。**"""
    out = [BEGIN, f"{ITEM} meta"]

    def emit(k: str, v) -> None:
        s = "" if v is None else str(v)
        first, *rest = s.split("\n")
        out.append(f"{k}: {first}")
        out.extend(f"+ {r}" if r else "+" for r in rest)

    emit("unit", obj.get("unit"))
    emit("base", obj.get("base"))
    kind_of = {"findings": "finding", "undecidable": "undecidable",
               "not_findings": "not_finding"}
    for sect in LIST_SECTIONS:
        req, _ = SCHEMA[sect]
        for it in obj.get(sect) or []:
            out.append(f"{ITEM} {kind_of[sect]}")
            for k in req:
                if k in it:
                    emit(k, it[k])
            for k in it:
                if k not in req:
                    emit(k, it[k])
    if obj.get("naming_variants_tried"):
        out.append(f"{ITEM} variants")
        for v in obj["naming_variants_tried"]:
            emit("tried", v)
    if obj.get("self_check"):
        out.append(f"{ITEM} self_check")
        for k, v in obj["self_check"].items():
            emit(k, "yes" if v is True else "no" if v is False else v)
    out.append(END)
    return "\n".join(out)
