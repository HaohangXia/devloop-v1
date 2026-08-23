#!/usr/bin/env bash
#  ⭐ DEVLOOP-GATES-REV: 9   ← 2026-08-12 对齐
#  ⚠️ rev 8 的内容是「四条摆设写法」第 4 条（判退出码 = 在有历史红的仓库上必然红）
#     与「闸的三种形状」。⭐ **本仓不需要改代码**，因为实跑绿检通过：
#     `devloop gates --project C:/pg/_infra/devloop --commit HEAD` → **5 过 / 0 未过，退出码 0**
#     （2026-08-12 实测）—— 本仓没有「改前就红」，所以判退出码在这里不是必然红。
#     ⛔ 但这一行不是免死金牌：哪天 pytest 出现一条长期红，这道闸就得改成基线对照形。
#  ⭐ DEVLOOP-PREFLIGHT: 1   ← 声明「我支持开跑前验判据」
#     ⚠️ 本仓**没有** VOID 那一支，而这是对的：模板里那一支是给
#     「`.devloop/` 未被 git 跟踪」的项目用的，⭐ 而本仓跟踪它
#     （`git ls-files .devloop` 有输出），所以「禁改清单」在这里真有东西可验。
#     ⛔ 别照抄模板把 void 加进来——那会造出一条永远走不到的分支。
# DevLoop 自身的验收闸。用它给自己把关——也用它当写任务的快速试验场。
#
# 契约（SPEC.md §5.2）：$1 = 被检查的工作目录
#   退出码 0=全过 · 1=有闸未过 · 2=闸自身故障（环境坏了不等于活没干好）
#   stdout 每行：<PASS|FAIL|SKIP>\t<闸名>\t<说明>
#
# ⚠️ 本文件受宪法保护：工人不得修改（改动即判失败）。

set -uo pipefail

WORK="${1:-}"
[ -z "$WORK" ] && { echo "用法: gates.sh <工作目录>" >&2; exit 2; }
[ -d "$WORK" ] || { echo "工作目录不存在: $WORK" >&2; exit 2; }

PY="${DEVLOOP_PYTHON:-python}"

fail=0
pass() { printf 'PASS\t%s\t%s\n' "$1" "$2"; }
bad()  { printf 'FAIL\t%s\t%s\n' "$1" "$2"; fail=1; }
skip() { printf 'SKIP\t%s\t%s\n' "$1" "$2"; }

# ── ⭐⭐ 体检模式（DEVLOOP-PREFLIGHT: 1）─────────────────────────
#
# ⛔ 为什么工具仓**自己**也要装这个：2026-08-11 把体检模式搬进模板时，
#    我只改了 `templates/gates.sh`，⚠️ **忘了工具仓自己装的这一份**。
#    ⭐ 是当天刚做出来的那道守卫（`doctor::gates_template_drift` 的「领先」分支）
#    当场把我抓住的 —— 它报「本项目 rev 4，模板已到 rev 7（落后 3 版）」。
#    ⚠️ 这恰好是同一个形状的又一次：**同一件事有两条路，漏掉的是没人盯着的那条。**
#    ⭐ 而这次有人盯着了，代价是 0。
#
# ⭐ 契约见 `templates/gates.sh` 的同名段落。要点：
#    收到 `DEVLOOP_PREFLIGHT=1` → ⛔ 不跑任何大考，只验自己的前提，
#    每道闸打一行 `PREFLIGHT<TAB>闸名<TAB>OK|BAD|VOID<TAB>说明`，然后立即退 0。
pf() { printf 'PREFLIGHT\t%s\t%s\t%s\n' "$1" "$2" "$3"; }

if [ "${DEVLOOP_PREFLIGHT:-0}" = "1" ]; then
  #  ⚠️ 本仓**没有**靠外部对照物的闸（没有改前存档 / 答案卷），
  #     ⛔ 所以这里不该编一个出来 —— 报清楚「我这几道靠什么」就够了。
  if [ -z "${WORK:-}" ] || [ ! -d "$WORK" ]; then
    pf "环境自检" "BAD" "⛔ 工作目录不存在：${WORK:-<空>}"
  else
    pf "环境自检" "OK" "工作目录在"
    #  ⭐⭐ 2026-08-16 补：以前这里只报 3 道，而真跑时会打判定行的有 6 道。
    #     ⛔ 差集里那 4 道（语法/改动守卫/测试守卫/禁改清单）一旦被 require_pass
    #     点名，`cli.py::_preflight_criteria` 会当场拒单——而它们本身好好的。
    #     实测：7 份计划里 6 份因此被拒，只有一道都没点名的 demo 放行。
    #  ⚠️ 这几道**不靠外部对照物**（判据全在仓内），所以体检能给的答案只有
    #     「它在不在」，⛔ 给不了「它会不会过」——那要真跑才知道。
    #     ⭐ 而体检要回答的本来就是「如果现在真跑，我这一道会打哪一档」，
    #     不是「我会不会过」。报得出名字，点名它才不会被误拒。
    pf "语法" "OK" "devloop/ 在——真跑时逐个编译，判据全在仓内"
    #  ⛔ 这三道**不许**去看 `DEVLOOP_CLEAN_CHECKOUT`。
    #     体检要回答的是「**真跑**时我会打哪一档」，而真跑**一律**在干净检出里
    #     （`cli.py::_run_unit` 与 `gates.py::run_on_commit` 都硬写 clean_checkout=True）；
    #     ⚠️ 而体检自己跑在项目目录上、这个标记天然是 0。
    #  ⇒ 照标记分支的话，这三道**永远**报 VOID —— 答的是「在这个脏工作区会怎样」，
    #     ⛔ 那是另一个问题，而且会把整份体检拖成 unknown。
    pf "改动守卫" "OK" "真跑在干净检出里，这道会验"
    pf "测试守卫" "OK" "真跑在干净检出里，这道会验"
    pf "禁改清单" "OK" "真跑在干净检出里，这道会验"
    if [ -d "$WORK/tests" ]; then
      pf "pytest" "OK" "tests/ 在（⚠️ 本闸不靠外部对照物，判据全在仓内）"
    else
      pf "pytest" "BAD" "⛔ 找不到 tests/ —— 全量测试那道闸必然验 0 条"
    fi
    if [ -d "$WORK/devloop" ]; then
      pf "被测代码来源" "OK" "devloop/ 在"
    else
      pf "被测代码来源" "BAD" "⛔ 找不到 devloop/"
    fi
  fi
  exit 0
fi


# ── 闸 0 · 环境自检（失败 = 退出码 2）──────────────────────────
#
# ⛔⛔ 2026-08-16：这一段以前**只 exit，一行判定都不打** —— 而体检里报着
#    「环境自检」这个名字。⇒ 点名它的计划会**体检放行、花完钱之后**才收
#    「闸与验收契约对不上」（退出码 2）。
#    ⭐ 这正是体检存在的理由的反面：体检报得出的名字，真跑时必须也报得出。
# ⚠️ 环境坏了仍然退 2（那是「闸自身故障」，不是「活没干好」），
#    ⛔ 但退之前必须先把判定行打出来，否则下游只看到一个没有内容的失败。
if ! command -v "$PY" >/dev/null 2>&1; then
  bad "环境自检" "找不到 python（可用 DEVLOOP_PYTHON 覆盖）"; exit 2
fi
if [ ! -d "$WORK/devloop" ]; then
  bad "环境自检" "$WORK 下没有 devloop/ 包目录"; exit 2
fi
pass "环境自检" "python 在 · devloop/ 在"

# ── 闸 1 · 语法与导入（最便宜的检查放最前，坏了后面都白跑）─────
if out=$(cd "$WORK" && "$PY" -c "
import pathlib, py_compile, sys
bad = []
for f in sorted(pathlib.Path('devloop').rglob('*.py')):
    try: py_compile.compile(str(f), doraise=True)
    except Exception as e: bad.append(f'{f}: {e}')
print('\n'.join(bad))
sys.exit(1 if bad else 0)
" 2>&1); then
  pass "语法" "devloop/ 下全部 .py 可编译"
else
  bad "语法" "$(printf '%s' "$out" | head -1)"
fi

# ── 闸 2 · 改动守卫（仅在干净检出里有意义，见 BACKLOG G-07）────
if [ "${DEVLOOP_CLEAN_CHECKOUT:-0}" != "1" ]; then
  skip "改动守卫" "工作区有未提交改动，无法区分工人改动与在途改动"
else
  #  ⛔⛔ 2026-08-16：这一行以前不存在 —— 于是「改动守卫」这个名字
  #     **只在它被跳过时才出现**，干净检出（= 真跑的唯一形态）下从不露面。
  #     ⇒ 它是一个**谁也没法点名的名字**：写进 require_pass 必收退出码 2。
  #  ⭐ 实测发现的路子值得记下来：先给体检补上它，再真跑一次闸——
  #     真跑那 6 行里没有它，当场暴露。⛔ 只读脚本读不出来。
  pass "改动守卫" "干净检出——下面两道（测试守卫 / 禁改清单）分别验"
  # ── 工人不得改测试来让自己过关——「闸自己放自己过」的等价物 ──
  #
  # ⚠️ 2026-07-29 修正判据。原判据是「tests/ 一个字都不许动」，它挡住了作弊，
  #    但**连合法写测试也一起挡了**——而 TDD 是本项目的硬要求。实测：一单
  #    「加个函数并补两条测试」的正常活，工人干得完全正确，却被这道闸判失败。
  #    ⛔ 那是第三种假绿的镜像：判据的维度错了。要防的是「**改**已有断言」，
  #    不是「碰过 tests/」。
  #
  # 新判据：**只许新建 test_*.py，不许改动已有文件。**
  #   · 改/删/改名已有测试 → 失败（削弱断言的唯一途径）
  #   · 新建 test_*.py     → 放行（新增断言只会更严，不会更松）
  #   · 新建其它文件       → 失败。⛔ 这条专堵 `tests/conftest.py`：
  #     conftest 里一个 autouse fixture 就能把整包已有测试架空，
  #     那是**新建文件**却能削弱已有断言的唯一后门。
  #
  # ⚠️ 已知残余漏洞（不装作没有）：新建的 test_*.py 在被收集时会执行模块级
  #    代码，理论上可在 import 期给 devloop 打补丁，影响同场次其它测试。
  #    这需要蓄意破坏，而本闸防的是「顺手把断言改松」。彻底堵法是拿**基线那份
  #    tests/** 去跑工人的代码，见 BACKLOG G-58。
  # ⛔ `-c core.quotepath=off` 不是可选项：git 默认把非 ASCII 路径转义成
  #    "tests/test_\345\267\245...py"（**带引号**），于是下面按 `\.py$` 匹配的
  #    模式一律对不上——中文命名的新测试会被误判成「非 test_*.py 的可疑文件」。
  #    2026-07-29 由 test_新建测试文件必须放行 抓到；同一个坑 constitution.py 栽过一次。
  dirty_tests=$(cd "$WORK" && git -c core.quotepath=off status --porcelain -- 'tests/' 2>/dev/null | head -20)
  if [ -z "$dirty_tests" ]; then
    pass "测试守卫" "tests/ 未被改动"
  else
    # 改动了已有文件：状态码首两列含 M/D/R；新增则是 ?? 或 A
    changed=$(printf '%s\n' "$dirty_tests" | grep -Ev '^(\?\?|A ) ' | head -5)
    # 新增里凡不是 test_*.py 的一律拦下（conftest.py / pytest.ini / 数据文件）
    sneaky=$(printf '%s\n' "$dirty_tests" | grep -E '^(\?\?|A ) ' \
             | sed 's/^...//' | grep -Ev '(^|/)test_[^/]*\.py$' | head -5)
    if [ -n "$changed" ]; then
      bad "测试守卫" "工人**改动了已有测试**——改测试让自己过关不算通过：$(echo "$changed" | tr '\n' ' ')"
    elif [ -n "$sneaky" ]; then
      bad "测试守卫" "工人在 tests/ 下新建了非 test_*.py 文件——conftest 之类能架空已有断言：$(echo "$sneaky" | tr '\n' ' ')"
    else
      n=$(printf '%s\n' "$dirty_tests" | grep -cE '^(\?\?|A ) ')
      pass "测试守卫" "已有测试一个字未改；新增 $n 个 test_*.py（新增断言只会更严）"
    fi
  fi

  # ⚠️ 本守卫的正确语义是「工人不得**改动**它」，而非「它不该存在」。
  # 两种项目状态都要覆盖，否则守卫要么恒真要么恒假（都等于没守）：
  #   · `.devloop/` 被 git 跟踪（如 DevLoop 自己）→ 用 git status 查改动
  #   · `.devloop/` 未被跟踪（如 eco-ob）→ worktree 里本就不该出现它
  # 2026-07-26 两次踩坑：先是恒 PASS（未跟踪时永远为空），
  # 改成「存在即失败」后，又在已跟踪的项目上恒 FAIL。
  if git -C "$WORK" ls-files --error-unmatch .devloop >/dev/null 2>&1; then
    dirty_prot=$(cd "$WORK" && git status --porcelain -- '.devloop/' 2>/dev/null | head -3)
    if [ -n "$dirty_prot" ]; then
      bad "禁改清单" ".devloop/ 被改动——工人不得修改自己的规则与闸"
    else
      pass "禁改清单" ".devloop/ 已跟踪且未被改动"
    fi
  elif [ -e "$WORK/.devloop" ]; then
    bad "禁改清单" "工人在 worktree 里创建了 .devloop/ —— 该项目未跟踪它，它不该出现"
  else
    pass "禁改清单" "worktree 内无 .devloop/（该项目未跟踪它，符合预期）"
  fi
fi

# ── 闸 3 · 被测代码来源校验 ───────────────────────────────────
# ⚠️ 本项目用 `pip install -e .` 安装，包链接指向**原目录**。
#    若测试加载的是原目录而非 worktree，闸就会静默地测错代码——
#    工人把源码改坏，闸照样全绿。这是「空守卫」的又一种形态，
#    而且比空守卫更危险：它会给出**看起来正确的绿**。
#    `python -m pytest` 会把 cwd 放进 sys.path 首位，故当前可行；
#    但这依赖调用形式，换成裸 `pytest` 就会失效。因此显式验一次。
loaded=$(cd "$WORK" && "$PY" -c "import devloop, pathlib; print(pathlib.Path(devloop.__file__).resolve().parent.parent)" 2>&1)
expect=$(cd "$WORK" && "$PY" -c "import pathlib; print(pathlib.Path('.').resolve())" 2>&1)
if [ "$loaded" = "$expect" ]; then
  pass "被测代码来源" "加载自 worktree 本身"
else
  bad "被测代码来源" "测试将加载 $loaded 而非 $expect —— 闸会测错代码，结论无效"
fi

# ── 闸 4 · 全量测试 ───────────────────────────────────────────
# ⛔ 超时阈值不许贴着基线设。
#    原来是 120 秒，注释里的基线写着「38 个测试 / 0.3 秒」——余量 400 倍。
#    2026-07-29 实测已是 **348 个测试 / 30 秒**，余量只剩 4 倍。而测试守卫
#    刚刚**明确放行工人新建 test_*.py**，所以测试数只会继续涨。
#    机器有负载、或工人一次补十几条测试，就能把它推过阈值 → rc=124 →
#    闸判 FAIL 且理由写「有东西卡死了」——**归因直接错**，
#    而 retries 会拿同一个错理由把整个阶段耗死。
#    ⚠️ 别再往注释里写死具体基线数字，它必然过时；下面的文案也不再声称基线。
# 对比 eco-ob 的 486 秒——这正是选它当试验场的理由：迭代快两千倍。
if out=$(cd "$WORK" && timeout "${DEVLOOP_TEST_TIMEOUT:-600}" "$PY" -m pytest -q 2>&1); then
  pass "pytest" "$(printf '%s' "$out" | tail -1)"
else
  rc=$?
  if [ $rc -eq 124 ]; then
    bad "pytest" "超时（>${DEVLOOP_TEST_TIMEOUT:-600}s）——⚠️ 可能是卡死，也可能只是测试变多了。先跑一次 pytest 看实际耗时再下结论"
  else
    bad "pytest" "$(printf '%s' "$out" | grep -E '^(FAILED|ERROR)' | head -3 | tr '\n' ' ')"
  fi
fi

exit $fail
