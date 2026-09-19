<p align="right"><a href="./README.md">English</a> · <strong>中文</strong></p>

# 运维工具

这些脚本放在 Python 包之外，因为它们属于宿主侧控制与探针，而不是可导入的产品模块。

| 文件 | 用途 |
|---|---|
| [`devloop-mode-hook.py`](devloop-mode-hook.py) | 在模型处理 prompt 前识别用户显式模式触发。 |
| [`devloop-spend-gate.py`](devloop-spend-gate.py) | 没有有效模式 token 时拒绝消耗额度的命令。 |
| [`probe_ledger_concurrency.py`](probe_ledger_concurrency.py) | 复现台账并发写入行为。 |
| [`test_mode_gate.py`](test_mode_gate.py) | 针对模式闸字面命令场景的独立测试工具。 |

Python 包不会注册这些 hook。机器级注册、绝对路径耦合与 fail-open 限制记录在根目录的
[`README.zh-CN.md`](../README.zh-CN.md) 和 [`SPEC.md` 第 2 节与第 5.9 节](../SPEC.md)；把任何
hook 安装到真实环境前，请先阅读这些边界。
