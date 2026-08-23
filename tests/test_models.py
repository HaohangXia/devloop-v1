"""跨进程边界的数据契约。

这些字段来自外部进程的 JSON，字段缺失或语义搞错**不会报错，只会静默出假数据**——
本项目已经栽过一次（台账在跑闸前记账，把「工人跑完了」记成「这单成功了」，
统计显示 100% 成功而实际 50%）。故这一层的测试优先级最高。
"""

from __future__ import annotations

import pytest

from devloop.models import Receipt, TaskSpec, WorkerConfig


class TestTaskSpec:
    def test_四段齐全时可加载(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("# 角色\nA\n# 任务\nB\n# 禁令\nC\n# 报告格式\nD", encoding="utf-8")
        assert TaskSpec.load(f).name == "t"

    @pytest.mark.parametrize("missing", ["# 角色", "# 任务", "# 禁令"])
    def test_缺必需段落必须拒绝加载(self, tmp_path, missing):
        """任务书缺禁令段就派出去 = 让工人在没有边界的情况下动手。宁可不派。"""
        body = "\n".join(s + "\nx" for s in ("# 角色", "# 任务", "# 禁令") if s != missing)
        f = tmp_path / "bad.md"
        f.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="缺少必需段落"):
            TaskSpec.load(f)


class TestReceipt:
    def test_模型核对_命中(self):
        r = Receipt(modelUsage={"deepseek-v4-pro[1m]": {}})
        assert r.ran_on("deepseek-v4-pro")

    def test_模型核对_不符时必须为假(self):
        """派单最要命的失效模式：以为跑在便宜模型上，实际跑在贵的上，账单静默翻倍。"""
        r = Receipt(modelUsage={"claude-opus-5": {}})
        assert not r.ran_on("deepseek-v4-pro")

    def test_缓存命中读数(self):
        r = Receipt(usage={"cache_read_input_tokens": 7424})
        assert r.cache_read_tokens == 7424

    def test_无缓存字段时归零而非报错(self):
        assert Receipt().cache_read_tokens == 0

    def test_上游多出的字段不应导致解析失败(self):
        """回执来自外部进程，上游加字段是常态；因此崩掉等于把工具绑死在某个版本上。"""
        r = Receipt(**{"is_error": False, "result": "ok", "某个未来新增字段": 1})
        assert r.result == "ok"


class TestWorkerConfig:
    def test_env_映射齐全(self):
        c = WorkerConfig(base_url="https://x/anthropic", model="m",
                         auth_token="secret", timeout_s=300)
        env = c.env()
        assert env["ANTHROPIC_BASE_URL"] == "https://x/anthropic"
        assert env["ANTHROPIC_MODEL"] == "m"
        assert env["API_TIMEOUT_MS"] == "300000", "超时单位是毫秒，写错会导致工人被提前杀"

    def test_密钥不出现在_repr(self):
        """日志与报错会打印对象；密钥泄进日志比泄进代码更难发现。"""
        c = WorkerConfig(base_url="u", model="m", auth_token="sk-SECRET-VALUE")
        assert "sk-SECRET-VALUE" not in repr(c)


# ── 任务书标题：**整行匹配，不是子串** ──────────────────────────
# ⛔ 第三轮审查的连带发现（判据的维度错了，第三种假绿）。
#    `"# 角色" in body` 是子串检查，实测四种写法里**三种静默通过**：
#      `## 角色`（二级标题）· `# 角色与身份`（加后缀）· 正文里只是**提到**这个词
#    最后一种最坏：一份**根本没有角色段**的任务书，只要正文里引用过这五个字就放行。
#    ⚠️ 实测 28 份真实任务书在严格判据下零份被判红——收紧是安全的。

import pytest as _pt


@_pt.mark.parametrize("head,ok", [
    ("# 角色", True),
    ("## 角色", False),
    ("# 角色与身份", False),
    ("#角色", False),
    ("  # 角色", False),
])
def test_任务书的段落标题必须整行匹配(tmp_path, head, ok):
    from devloop.models import TaskSpec
    f = tmp_path / "t.md"
    f.write_text(f"{head}\nx\n\n# 任务\ny\n\n# 禁令\nz\n", encoding="utf-8")
    if ok:
        assert TaskSpec.load(f).name == "t"
    else:
        with _pt.raises(ValueError) as e:
            TaskSpec.load(f)
        assert "# 角色" in str(e.value)


def test_正文里提到标题不算有那一段(tmp_path):
    """⛔ 最坏的一种：任务书**根本没有角色段**，只是正文里引用过这五个字。
    子串判据会放行它，而工人拿到的是一份缺段落的任务书。"""
    from devloop.models import TaskSpec
    f = tmp_path / "t.md"
    f.write_text("随便写\n这里引用一下 # 角色 这个词\n\n# 任务\ny\n\n# 禁令\nz\n",
                 encoding="utf-8")
    with _pt.raises(ValueError):
        TaskSpec.load(f)
