"""订阅后端：用你的 Claude 订阅起子进程跑贵模型（2026-07-29）。

## ⭐ 这条路解开了整个项目的死结

在此之前的两难是：

| | 无人值守 | 质量 |
|---|---|---|
| `api` 后端（付费 key + 便宜模型） | ✅ | ❌ 实测差 3 倍，且加上拆单成本还更贵 |
| `subagent` 后端（贵模型） | ❌ 要人起子代理 | ✅ |

而**根子只是一个过期两个月的令牌**：`~/.claude/.credentials.json` 里的
`accessToken` 停在 2026-05-27，于是 `claude -p` 起的子进程一律 401。
当初据此断定「子代理不能当子进程后端」，只剩付费 API 这一条路。

2026-07-29 重新登录之后实测：**子进程跑通了，模型是 claude-opus-4-7，
工具白名单生效，工作目录生效**。于是无人值守与质量可以同时成立。

## ⛔ 三件与 api 后端**必须不一样**的事

1. **不加 `--bare`。** `--bare` 的用途正是**跳过 OAuth**去用环境变量里的 key，
   而订阅后端要的恰恰是 OAuth。加了它，订阅就用不上。
2. **不注入 `ANTHROPIC_*` 环境变量。** 注入了会把请求打到第三方端点。
3. **成本口径不同。** 回执里的 `total_cost_usd` 是 Opus 价目表算的**合成价**
   （实测 12 个 token 报 $0.42）。订阅制下**不花美元，花的是额度**——
   记 0.0 而不是 None，⛔ 但必须在台账里标明「走订阅额度，不是免费」。
"""

from __future__ import annotations

import pytest

from devloop.config import ConfigError


# ══ 后端形态 ═══════════════════════════════════════════════════

def test_订阅后端不许加bare():
    """⛔ `--bare` 的用途是**跳过 OAuth** 去用环境变量里的 key。
    订阅后端要的恰恰是 OAuth——加了它，订阅就用不上，
    而且会掉回「读环境变量里那个 key」，那是另一条路线的行为。"""
    from devloop import backends
    from devloop.dispatch import build_cmd
    b = backends.Backend(name="sub", kind="subscription", model="claude-opus-4-7",
                         source="test")
    cmd = build_cmd(b.worker_config(), tools="readonly", max_turns=10,
                    prompt="x", exe="claude")
    assert "--bare" not in cmd, f"订阅后端不许带 --bare：{cmd}"
    assert "-p" in cmd and "--output-format" in cmd


def test_api后端仍然要带bare():
    """⚠️ 防回归：`api` 后端**必须**带 `--bare`——不带的话，
    过期的 OAuth 凭据会**劫持**掉环境变量里的 key（记录在 TROUBLESHOOTING 故障 4）。"""
    from devloop import backends
    from devloop.dispatch import build_cmd
    b = backends.Backend(name="ds", kind="api", model="m", source="test",
                         base_url="http://x", auth_token="t")
    cmd = build_cmd(b.worker_config(), tools="readonly", max_turns=10,
                    prompt="x", exe="claude")
    assert "--bare" in cmd


def test_订阅后端不许注入端点与密钥():
    """⛔ 注入 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` 会把请求
    打到第三方端点——那就不是「用订阅」了。"""
    from devloop import backends
    b = backends.Backend(name="sub", kind="subscription", model="claude-opus-4-7",
                         source="test")
    env = b.worker_config().env()
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        assert k not in env, f"订阅后端不该注入 {k}，实得 {env}"


def test_订阅后端算是能起子进程的():
    """⚠️ `subagent` 起不了子进程（那是交接协议），`subscription` 起得了。
    这两者别混——它们的执行方式完全不同。"""
    from devloop import backends
    sub = backends.Backend(name="s", kind="subscription", model="m", source="t")
    sa = backends.Backend(name="a", kind="subagent", model="m", source="t")
    assert sub.can_subprocess
    assert not sa.can_subprocess


def test_订阅后端支持写操作(tmp_path):
    """⭐ 与 `subagent` 的关键区别：订阅后端是**真子进程**，
    有自己的工作目录，所以能被放进隔离 worktree——写操作因此是安全的。
    （`subagent` 跑在编排方的工作目录里，那才是它被禁止写的原因。）"""
    from devloop import backends, handoff
    b = backends.Backend(name="s", kind="subscription", model="m", source="t")
    handoff.guard_write_tools(b, "implement")     # 不该抛
    handoff.guard_write_tools(b, "full")


# ══ ⛔ 凭据过期：`auth status` 会撒谎 ═══════════════════════════

def test_访问令牌过期但有refreshToken时不许拦(tmp_path):
    """⛔ **这条测试推翻了它自己的上一版。**

    上一版断言的是「访问令牌过期 → `ok=False` → 拒绝派单」。
    2026-07-29 18:46 实测把它否掉了：令牌显示 **10 小时前就过期**（08:22），
    然而 `claude -p "回答一个字：好"` 正常返回，跑完文件里的 `expiresAt`
    自动跳到次日 02:46——**CLI 用 refreshToken 自己续了期，全程不需要人**。

    ⚠️ 这是**改测试而不是改实现**的少数正当情形：不是实现没满足测试，
    是测试断言的那条判据与现实不符。旧断言会拦下一批**本来能跑通**的活，
    比不检查更坏——它以「凭据过期」的名义停机，而真因是判据错了。

    ⭐ 对无人值守的意义：一次登录只管 8 小时，但每次派单都是新子进程、
    都会按需自动续期，所以 **8 小时这条线不再是跑一夜的障碍**。
    """
    import json
    import time
    from devloop import credentials as cred
    f = tmp_path / ".credentials.json"
    f.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "x" * 108, "refreshToken": "y" * 108,
        "expiresAt": int((time.time() - 10 * 3600) * 1000),   # 10 小时前过期
        "subscriptionType": "max"}}), encoding="utf-8")
    st = cred.check(f)
    assert st.ok, f"有 refreshToken 就不该拦——实得 detail={st.detail}"
    assert st.will_refresh, "要标明「CLI 会自己续」，否则人看不懂为什么放行"


def test_过期且没有refreshToken才判死(tmp_path):
    """⚠️ 放宽之后仍要守住的那一半：**续不了就是真的跑不了**。
    没有 refreshToken 的过期令牌，派出去只会收回 401——那时拦下才是对的。"""
    import json
    import time
    from devloop import credentials as cred
    f = tmp_path / ".credentials.json"
    f.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "x" * 108,
        "expiresAt": int((time.time() - 3600) * 1000),
        "subscriptionType": "max"}}), encoding="utf-8")
    st = cred.check(f)
    assert not st.ok
    assert "auth login" in st.detail, "必须给出该跑哪条命令"


def test_快过期只提示不拦(tmp_path):
    """⚠️ `expiring_soon` 保留，但语义降级为**提示**——它不再是拒派的理由。
    实测已证明跨过这条线 CLI 会自己续，据此停机是误拦。"""
    import json
    import time
    from devloop import credentials as cred
    f = tmp_path / ".credentials.json"
    f.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "x", "refreshToken": "y",
        "expiresAt": int((time.time() + 600) * 1000),     # 10 分钟后过期
        "subscriptionType": "max"}}), encoding="utf-8")
    st = cred.check(f)
    assert st.ok and st.expiring_soon


def test_凭据没问题时不加噪音(tmp_path):
    """⚠️ 防回归：正常状态下别每次都刷警告——天天警告等于没有警告。"""
    import json
    import time
    from devloop import credentials as cred
    f = tmp_path / ".credentials.json"
    f.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "x", "refreshToken": "y",
        "expiresAt": int((time.time() + 7 * 3600) * 1000),
        "subscriptionType": "max"}}), encoding="utf-8")
    st = cred.check(f)
    assert st.ok and not st.expiring_soon


def test_凭据文件不存在要说人话(tmp_path):
    from devloop import credentials as cred
    st = cred.check(tmp_path / "根本没有")
    assert not st.ok and "auth login" in st.detail


# ══ 成本口径：订阅制不花美元，花额度 ═══════════════════════════

def test_订阅制的美元成本是零而不是未知():
    """⛔ 这条很要紧：自动驾驶有一条防线是「**算不出成本就停**」
    （因为「算不出」被当成「花了 0 元」会让预算永远不耗尽）。

    订阅制下美元成本**确实是 0**——那不是「算不出」，是「已包含在订阅里」。
    记成 None 会让自动驾驶第一单就停机。

    ⚠️ 但必须标明「走订阅额度，不是免费」——额度是真实的稀缺资源。
    """
    from devloop.pricing import real_cost_usd
    assert real_cost_usd("__subscription__", {"input_tokens": 1000,
                                              "output_tokens": 500}) == 0.0


def test_回执里的合成价不许被当成真钱():
    """⚠️ 实测：12 个 token 的回执报 `total_cost_usd: 0.42`。
    那是 Opus 价目表算的合成价（G-28 的老问题），订阅制下完全不成立。"""
    from devloop.pricing import price_source
    assert "订阅" in price_source("__subscription__")


# ══ ⛔ 接线：函数对了不等于路径对了 ═══════════════════════════════

def test_订阅派单记的账里成本必须是零而不是未知(tmp_path):
    """⛔ **这条是端到端实跑抓出来的**（2026-07-29，第一单真派）。

    `test_订阅制的美元成本是零而不是未知` 直接调 `real_cost_usd("__subscription__")`
    拿到 0.0，绿了。但真派一单，台账里记的是：

        cost_usd_real  None
        price_source   未知（价目表里没有这个模型）

    **函数是对的，路径是断的**——`telemetry.record(..., model=cfg.model)`
    把**模型名** `claude-opus-4-7` 当价目 key 传了进去，而订阅的 key 是
    `__subscription__`。这是六种假绿里的第二种：**实现了但没接线**，
    我上一条测试恰好绕过了断掉的那一段。

    ⚠️ 后果不是记错一个数字：自动驾驶有一条防线是「**算不出成本就停**」，
    它读的正是 `cost_usd_real`。记成 None，订阅无人值守**第一单就停机**
    ——而无人值守正是这条路线存在的全部理由。
    """
    import json
    from devloop import backends, telemetry
    from devloop.dispatch import DispatchResult
    from devloop.models import Receipt

    b = backends.Backend(name="sub", kind="subscription",
                         model="claude-opus-4-7", price_key="__subscription__",
                         source="test")
    cfg = b.worker_config()
    r = Receipt(is_error=False, total_cost_usd=0.42, num_turns=3,
                usage={"input_tokens": 1000, "output_tokens": 500},
                modelUsage={"claude-opus-4-7": {}})
    led = tmp_path / "t.jsonl"
    telemetry.record(led, DispatchResult("u", r, None), model=cfg.model,
                     price_key=cfg.pricing_key(), tools="implement")
    row = json.loads(led.read_text(encoding="utf-8").strip())

    assert row["cost_usd_real"] == 0.0, (
        f"订阅制美元成本是 0，不是「未知」。实得 {row['cost_usd_real']}"
        "——自动驾驶会因此第一单就停机。")
    assert "订阅" in row["price_source"]
    # ⚠️ 同时守住：`model` 字段记的必须仍是**真跑的模型**，不是价目 key。
    #    这两件事以前挤在一个参数里，改的时候很容易把台账的模型列写坏。
    assert row["model"] == "claude-opus-4-7"


def test_价目key缺省时仍退回模型名():
    """⚠️ 防回归：绝大多数后端的 price_key 是空的，此时必须退回模型名，
    否则这次改动会把**所有**既有后端的成本记成未知。"""
    from devloop import backends
    b = backends.Backend(name="ds", kind="api", model="deepseek-v4-pro",
                         source="t", base_url="http://x", auth_token="k")
    assert b.worker_config().pricing_key() == "deepseek-v4-pro"


def test_派单路径本身会把价目key传下去(tmp_path, monkeypatch):
    """⛔ **上一条测试自己就犯了它要抓的错。**

    `test_订阅派单记的账里成本必须是零而不是未知` 是**手动**把 `price_key=`
    传给 `record()` 的——它证明的是「record 收到 key 会算对」，
    **不证明 cli 真的会传**。而断掉的恰恰就是 cli 那一段。

    所以这条不碰 `record` 的参数，直接跑 `_run_unit`（只把真派单换成假回执，
    省钱不省路径），从台账里读结果。⚠️ 判据的维度必须落在**被怀疑的那一段**上。
    """
    import json
    from devloop import backends, cli
    from devloop.config import ProjectPaths
    from devloop.dispatch import DispatchResult
    from devloop.models import Receipt

    (tmp_path / ".devloop").mkdir()
    (tmp_path / ".devloop" / "rules-digest.md").write_text("x", encoding="utf-8")
    paths = ProjectPaths(project=tmp_path)
    b = backends.Backend(name="sub", kind="subscription", model="claude-opus-4-7",
                         price_key="__subscription__", source="test")

    fake = Receipt(is_error=False, num_turns=2, total_cost_usd=0.42,
                   usage={"input_tokens": 900, "output_tokens": 300},
                   modelUsage={"claude-opus-4-7": {}})
    monkeypatch.setattr(cli, "dispatch_one",
                        lambda *a, **k: DispatchResult("u", fake, None))

    class _Spec:
        name = "u"
    cli._run_unit(_Spec(), paths, b.worker_config(), tools="readonly",
                  max_turns=5, gate_fp="", writes=False)

    #  ⭐ 取**收工行**：一单从 G-108 起是两行，开跑行不带成本字段。
    row = [json.loads(l) for l in
           paths.telemetry.read_text(encoding="utf-8").splitlines()
           if l.strip() and json.loads(l).get("event") != "open"][-1]
    assert row["cost_usd_real"] == 0.0, (
        f"派单路径没把价目 key 传下去，成本记成 {row['cost_usd_real']}")
    assert row["model"] == "claude-opus-4-7", "台账的 model 列必须仍记真跑的模型"


def test_doctor默认不跑真探针(monkeypatch):
    """⚠️ 真探针花额度，⛔ 不许因为「顺手体检一下」就悄悄花掉。
    默认必须是「没跑」，而不是「跑了但你不知道」。"""
    from devloop import credentials as cred, doctor
    called = []
    monkeypatch.setattr(cred, "probe", lambda *a, **k: called.append(1) or (True, "x"))
    checks = doctor._credentials(probe=False)
    assert not called, "默认不该起子进程"
    assert any(c.ok is None and "没跑" in c.detail for c in checks), \
        "要明说「没跑」，别让人以为体检过了"


def test_doctor探针与排除法不一致时必须说出来(monkeypatch):
    """⛔ 这是本模块最该报出来的一件事。

    `check()` 读文件、`probe()` 起真进程——它俩一旦分歧，说明 `check()` 的判据
    又和现实脱节了（今天已经脱节两次：先是 auth status，后是 expiresAt）。
    ⚠️ 分歧被压下去，下次就得靠一整批 401 才能发现。
    """
    from devloop import credentials as cred, doctor
    monkeypatch.setattr(cred, "check", lambda *a, **k: cred.CredStatus(
        False, False, "排除法说跑不了"))
    monkeypatch.setattr(cred, "probe", lambda *a, **k: (True, "探针说能跑"))
    checks = doctor._credentials(probe=True)
    assert any("不符" in c.name for c in checks), \
        f"分歧必须单列一行报出来，实得 {[c.name for c in checks]}"


def test_doctor的派单自检必须和真派单走同一条命令行():
    """⛔ **2026-07-29 交叉核对抓到：自检验的是当前不用的那条路。**

    `_dispatch_smoke` 自己拼了一套命令行，写死 `--bare` + `--output-format json`。
    而真派单早就改成了 `stream-json --verbose`，且订阅后端**绝不加 `--bare`**。
    于是这个自称「唯一能证明派单通道活着」的检查，验的是另一条路——
    它绿了也不说明默认后端能跑，它红了也不说明默认后端不能跑。

    ⚠️ 根因是**同一件事写了两遍**，两遍必然分叉。这个项目已经在
    `_rebuild_argv` 上栽过同一个坑（重建 argv 漏参数），当时的结论是
    「必须有一条不变量测试把分叉钉死」。这就是那条。
    """
    import inspect
    from devloop import doctor
    src = inspect.getsource(doctor._dispatch_smoke)
    assert "build_cmd" in src, (
        "⛔ 派单自检必须复用 dispatch.build_cmd，不许自己再拼一遍命令行——"
        "拼两遍必然分叉，而分叉之后自检验的就不是真派单那条路了。")
    assert '"--bare"' not in src, "⛔ 别写死 --bare：订阅后端绝不加它"
    assert '"json"' not in src, "⛔ 别写死输出格式：真派单用的是 stream-json"


def test_doctor自检必须用注册表的默认后端(monkeypatch):
    """⛔ **2026-07-29 实测：自检花了真钱去验一条当前不用的通道。**

    注册表默认后端已是 `subscription`（走订阅额度，$0），而 `doctor` 读的是
    旧的 `worker-*.json`（deepseek，**真花美元**）。于是 `doctor --project` 的
    「派单通道」那一项：

      · 验的是一条**当前根本不用**的路——绿了不说明默认后端能跑
      · 而且真花了 $0.0082，操作者以为自己只是做了次体检

    ⚠️ 这跟「命令行拼两遍」是同一个病的两层：一层是命令怎么拼，
    一层是拿谁的配置去拼。两层都得指向真派单实际走的那条路。
    """
    from devloop import backends, doctor
    monkeypatch.setattr(backends, "load", lambda *a, **k: backends.Registry(
        {"sub": backends.Backend(name="sub", kind="subscription",
                                 model="claude-opus-4-7",
                                 price_key="__subscription__", source="test")},
        {}, "sub", "test"))
    chk, cfg = doctor._worker_cfg()
    assert cfg is not None and cfg.subscription, \
        "⛔ 自检该拿注册表的默认后端（subscription），不是旧的 worker 文件"
    assert "sub" in chk.detail


def test_默认后端起不了子进程时自检要跳过而不是退回旧配置(monkeypatch):
    """⛔ 退回旧配置顶上，会让自检报「通道正常」——而它验的是另一条路。
    ⚠️ 判不了就说判不了（Check.ok = None），这是本项目的一条硬规矩。"""
    from devloop import backends, doctor
    monkeypatch.setattr(backends, "load", lambda *a, **k: backends.Registry(
        {"sa": backends.Backend(name="sa", kind="subagent", model="m",
                                source="test")}, {}, "sa", "test"))
    chk, cfg = doctor._worker_cfg()
    assert cfg is None, "起不了子进程就别给配置——给了就会去派单"
    assert chk.ok is None, "判不了要报「未知」，⛔ 不是绿也不是红"
