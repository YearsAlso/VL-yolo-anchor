# Design: gui-launch-scripts

> **状态**：已实施（as-built）
> **关联 spec**：[`spec.md`](spec.md)
> **实施栈**：跨栈（进程编排）
> **最后核对**：2026-09-27

## 一、执行摘要

设计的核心难点不是"怎么启动两个进程"，而是**怎么拿到真正该杀的那个 PID**，以及**怎么把前端的退出码诚实传出去**。前者靠 `exec` 让子 shell 原地变成 python 进程，后者靠显式捕获退出码而非依赖脚本自然结束。代价是两平台各写一份、清理手段不同（`trap` vs `taskkill /T`），任何一侧改行为都要同步另一侧。

## 二、关键设计决策

| 决策 | 选项 | 选择 | 理由 | 被否选项的代价 |
|------|------|------|------|---------------|
| 后端启动方式 | `uv run uvicorn …` / 直接 `.venv` python | **直接 `$BACKEND_EXE -m uvicorn`** | `$!` 拿到的就是 python 本体，`kill` 才不会杀不掉监听进程 | 经 `uv` 包装 ⇒ 跟踪的是 uv 父进程，杀父留子，8765 仍被占，下次启动直接失败 |
| POSIX 下如何保持 PID 为 python | 记录两层 PID / 子 shell 内 `exec` | **`( cd "$ROOT" && exec … ) &`**（`:30`） | `exec` 让后台子 shell **原地替换**为 python，`$!` 即真实 PID | 不 `exec` ⇒ `$!` 是 subshell，`kill` 只杀掉壳，uvicorn 逃逸 |
| 端口冲突时机 | 启动后观察失败 / **启动前探测** | **启动前探测** | 早失败、错误信息直白（"先停掉占用进程"） | 启动后失败 ⇒ 报错像"后端起不来"，把用户引向错误方向排查 |
| 就绪判定 | 固定 `sleep` / 轮询探活 | **轮询探活 + 进程存活双检** | 快机器不用白等，慢机器不会误杀；同时能识别"进程已崩" | 固定 sleep ⇒ 慢机器起不来、快机器白等；只探活不查进程 ⇒ 后端崩溃后仍空轮询到超时 |
| 探活端点 | `/api/health` / `/api/tasks` | **选了 `/api/tasks`（现状）** | 当时能顺带验证任务目录可读 | **该选择在启用鉴权后失效**：公开路径集合只有 `/api/health`，`/api/tasks` 返回 401 ⇒ 脚本误判未就绪并终止（账本 **L1**）。**正确选择是 `/api/health`** |
| `.venv` 缺失 | 自动 `uv venv` / 提示并非零退出 | **提示 + 非零退出** | 环境创建是用户决策（可能想指定解释器/extra），静默创建会造出不预期环境 | 静默创建 ⇒ 用户不知道为什么 GPU extra 没装上 |
| `node_modules` 缺失 | 提示 / 自动 `npm install` | **自动安装**（`--skip-install` 可关） | 前端依赖安装无歧义、无需决策，且首次体验直接可用 | 只提示 ⇒ 新手第一跑就失败 |
| 退出码 | 脚本总是退 0 / 传播 Vite 退出码 | **传播** | CI 与调用方需要真实结果 | 总退 0 ⇒ 构建失败被脚本掩盖，"脚本绿但页面打不开" |
| 清理挂点 | 显式 try/finally / 信号 trap | sh `trap cleanup EXIT INT TERM`；ps1 `taskkill /T /F` | Ctrl+C 也要清理，否则留下孤儿后端 | 只在正常路径清理 ⇒ 中断即泄漏 |

## 三、双平台差异表

| 能力 | `start_gui.sh` | `start_gui.ps1` |
|------|----------------|-----------------|
| 参数 | `--skip-install`（`:7`） | `-SkipInstall` 开关（`:5`） |
| 端口探测 | `lsof -nP -iTCP:8765 -sTCP:LISTEN`（`:21`） | `Get-NetTCPConnection -LocalPort 8765 -State Listen`（`:20`） |
| 进程存活 | `kill -0 "$BACKEND_PID"`（`:43`） | `$backend.HasExited`（`:44`） |
| 探活 | `curl -fs --max-time 1`（`:46`） | `Invoke-WebRequest -TimeoutSec 1 -UseBasicParsing`（`:46`） |
| 清理 | `kill` + `wait`，`trap … EXIT INT TERM`（`:34-38`） | `taskkill /PID <id> /T /F`（`:35`） |
| 行尾要求 | **必须 LF**（shebang 否则失效） | CRLF 无碍 |

两份脚本**逻辑必须等价**。只改一侧是本项目已知的重复维护风险：审查脚本类变更时必须同时读两份。

## 四、风险与已知缺口

| 风险 | 等级 | 说明与缓解 |
|------|------|-----------|
| **L1** 启用鉴权后探活 401 导致启动失败 | 🔴 | 改打 `/api/health` 即可；或把 `/api/tasks` 加入公开集合（**不推荐** —— 会让任务枚举绕过鉴权） |
| 双份脚本行为漂移 | 🟡 | 无自动一致性检查；靠 `code-review` 时强制两份同读 |
| `bash -n` / PS AST 解析未进 CI | 🔵 | 现 CI 无脚本语法检查步骤；`pre-commit` 也不覆盖根目录脚本 |
| `start_gui.sh` 需 LF | 🔵 | 已由 `.gitattributes` 的 `*.sh text eol=lf` 钉住（该文件属迁移期新增，见 `docs/memory/pm-memory.md` 变更影响记录） |

## 附录：关键位置速查

| 位置 | 用途 |
|------|------|
| `start_gui.sh:21` | 8765 占用前置探测 |
| `start_gui.sh:26-31` | `exec` 技巧与真实 PID 获取（注释即设计说明） |
| `start_gui.sh:38` | `trap cleanup EXIT INT TERM` |
| `start_gui.sh:41-54` | 双检轮询（进程存活 + 探活） |
| `start_gui.ps1:20` | `Get-NetTCPConnection` 端口探测 |
| `start_gui.ps1:33-35` | `HasExited` + `taskkill /T /F` 进程树清理 |
| `src/api_server.py:53` | `_PUBLIC_PATHS = {"/api/health"}`（L1 的判据） |
