<p align="right"><a href="./README.md">English</a> · <strong>中文</strong></p>

# 测试套件

测试覆盖命令接线、worktree 生命周期、验证闸行为、额度与墙钟时间控制、台账持久性、
后台任务、模板、文档漂移以及冻结评测工具链。

其中既有普通回归测试，也有明确的红/绿演示：绿色路径证明有效工作能够通过，红色路径证明
控制机制能够拒绝一个已知坏条件；两者都不能证明真实世界的所有故障已经被覆盖。

额度夹具由实际捕获的事件流整理而来，保留契约测试使用的事件结构和限额值；响应正文、
标识符、模型名和用量数字替换为示意值。本机路径、账号连接、已安装工具及其他无关的
环境元数据会在公开前删除。

在仓库根目录运行：

```bash
python -m pip install -e ".[dev]"
PYTHONUTF8=1 python -m pytest -ra
```

PowerShell 先设置 `$env:PYTHONUTF8 = "1"`，再运行 `python -m pytest -ra`。
子进程及 shell 闸测试需要 Git 和 Bash。部分历史测试依赖未公开的 `_review/` 语料或原本地
`eco-ob` 项目，缺少相应输入时会明确跳过。不需要模型凭据，也不需要派发真实工作进程。

测试数量及其适用范围见 [`../README.zh-CN.md`](../README.zh-CN.md)。
