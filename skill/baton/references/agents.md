# 各执行代理：擅长什么、怎么启动、踩过的坑

启动命令的模板在 `scripts/agent_catalog.json`，`dispatch.py run` 会按模板拼好。这里记的是模板之外要知道的事。

**状态说明：**
- "已实测"：作者的机器上真实跑通过一轮；
- "按文档"：只按官方文档写，第一次用要先派一个小试。

本机的具体路径和额度，写在 `local/本机配置.md`。

## 目录

1. 所有代理通用
2. Claude Code
3. Codex
4. Antigravity（agy，Gemini）
5. Grok Build
6. Gemini CLI、Qwen Code、OpenCode（按文档）

---

## 1. 所有代理通用

- **无人值守。**没人回答执行方的问题，任务书要写到不用问。含糊处让它自己决定、记进报告、继续做完。
- **工作目录。**执行方在任务文件夹里启动，报告和日志写在那里。要它改别处的文件，用 `--access workspace --workdir <目录>`。
- **只做点名允许的事。**有的代理（尤其 Codex）会把没点名的输入都当禁区，然后跳过那一步。所以可读范围要写宽，只把真正的禁止项列出来。
- **规模预估。**一次中等任务（几十个文件或十来篇论文）通常 10–60 分钟。超过一小时，考虑拆开。
- **每次调用都有固定开销**（系统提示、工具定义，常见 1 万 token 以上）。长规格写进文件，启动提示只说"读某文件并照做"。
- **停任务。**用 `dispatch.py stop`。它会连子进程一起停，并把状态记为 stopped。

## 2. Claude Code（已实测：参数被接受）

- **擅长：**
  - 需要判断的中等任务、代码、读文档和核对；
  - 当中控时自带子代理，最省事。
- **作为执行方：**
  - 模板用 `claude -p ... --permission-mode acceptEdits`；
  - 无头模式下没有人批准，acceptEdits 只放行改文件；
  - 要跑命令，用 `--extra=--allowedTools --extra="Bash(python:*)"` 这样逐类放行（`--extra` 的值以 `-` 开头时必须写成 `--extra=值`）。
- **登录：**
  - 命令行要单独登录过（在终端运行一次 `claude`，按提示登录）；
  - 桌面版自带的命令行不一定已登录，未登录时会报 "Not logged in"。
- **模型：**用别名 `opus`、`sonnet`、`haiku` 或完整模型 ID。

## 3. Codex（已实测）

- **擅长：**
  - 实验代码、数学推导的实现、数据管线、重跑、测试、溯源表、画图脚本；
  - 需要运行命令的格式转换。
- **脾气：**很少写错，但完全照字面执行，不会发散。
  - 规格要写全：技术选择、参数、边界情况、停止条件；
  - 让它一次做完：含糊处自己决定并记录，自测、自查、出报告。
- **沙箱（workspace-write）：**
  - `.git` 只读：写"改动留在工作区，不要碰 .git"，提交由中控做；
  - 工作区和 `--add-dir` 之外的目录可能连读都被拒：需要的解释器（如用户目录下的 Python）、数据目录，都要 `--add-dir`；
  - 默认不能联网：需要联网取数的交给有搜索的代理，或由中控先下载好放进 `输入/`。
- **输出：**Codex 的进度写在 stderr，`dispatch.py` 已把它并进 `运行日志.txt`。最后一条消息另存为 `最后消息.md`。
- **模型：**
  - `scan_agents.py` 读 `~/.codex/models_cache.json`；
  - 用第三方中转时，以中转实际支持的为准，可在个人配置里用 `models_file` 指定中转的模型目录；
  - 档位常见 low / medium / high / xhigh / max。规格写全的机械活用 high，难活用 xhigh。
- **隐私：**接第三方中转时，发给 Codex 的内容会经过中转方。不要让它读到私人材料。

## 4. Antigravity（agy，Gemini）（已实测）

- **擅长：**
  - 通读长文档、筛文献、对照全文逐条核对说法（高档模型）；
  - 不需要跑命令的机械改动（便宜模型）。
- **一律按离线任务处理。**输入由中控预先放进任务文件夹。
- **模型：**
  - `agy models` 列出全部模型，ID 里带档位，如 `gemini-4-neon-high`；
  - 也可能列出别家模型（Claude、GPT-OSS），这些计入 Google 额度。
- **已知问题：**
  - **无人值守时终端命令会被自动拒绝**，而且第一次被拒就结束整轮。这时 `response` 为空、退出码却是 0，stderr 里写着 "headless mode cannot prompt"。
    - 默认的 `--access job` 已在启动语里写明"只用文件工具"。
    - 确实要跑命令，用户同意后用 `--access job_shell`（`--mode auto`）。
    - 但有的账户没有 auto 模式，会提示 "--mode auto is not available for this account" 并退回默认模式，命令照样被拒。要跑命令的活，最好交给别的代理。
  - 纯文本输出在没有终端时会丢，模板已固定用 JSON 输出。
  - `--print-timeout` 要带单位（`60s`），`0` 表示不限时。
  - "backend returned an empty conversation id"：有时连着出现好几次，隔 20 秒左右再试，通常就好了。真正的额度用尽会明说 "Individual quota reached … Resets in …"。
  - **登录过期**：stderr 写着 "Authentication required. Please visit the URL to log in"，等 60 秒后报 "authentication failed or timed out" 退出。无人值守时没法完成，请用户在终端运行一次 `agy`，在浏览器里授权、把授权码粘回终端，然后再重派。授权码由用户自己粘贴，中控不要代填。
  - 不要用 `--dangerously-skip-permissions`。
  - **"全文笔记"可能名不副实。**阅读任务的报告必须有"读过哪些页"一栏，并核对文件名和题目是否对应。
- **额度按周计**，官方不公布具体数字。额度紧时，阅读类工作改派其他长上下文模型。

## 5. Grok Build（已实测）

- **擅长：**
  - 联网取回论文、复现包、网页、期刊规则；
  - 调研近期文献的写法；
  - 换一个厂商做交叉核对。
  - 不适合读长 PDF 和写实验代码。
- **幻觉：**公开评测里幻觉率偏高，产出必须抽查。
- **MCP：**它会加载用户配置的 MCP 服务，其中可能有带用户登录状态的浏览器。个人配置里要给它加：
  - `--disallowed-tools "CallMcpTool,FetchMcpResource,ListMcpResources,..."`
  - `--deny "MCPTool"`
  - 以及禁止目录的 `--deny "Read(<目录>/**)"` 等规则。
- **核对实际工具：**日志第一行 `available_commands` 列出实际可用的工具，可以核对禁用项有没有生效。
- **沙箱：**Windows 上 `--sandbox` 不生效，只能靠 deny 规则和任务书。
- **联网：**不需要联网的任务加 `--offline`（即 `--disable-web-search`）。
- **并行：**两个 Grok 进程同时跑过，没问题；三个以上没试过。
- **额度耗尽**时报 HTTP 402 "usage balance exhausted"。

## 6. Gemini CLI、Qwen Code、OpenCode（按文档，未实测）

- 模板按各自官方文档写，第一次用先派小试，看日志确认参数被接受、结果文件写出来了。
- **Qwen Code：**`--list-models` 为空，多半是没登录或没有额度。
- **OpenCode：**模型 ID 形如 `供应商/模型`，额度按模型所属的供应商算。
- 跑通后把 `agent_catalog.json` 里的 `status` 改成 `verified`，并在这里补上踩到的坑。
