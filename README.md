# Baton：让一个会话当中控，调度本机的多个编码代理

> Baton 既是指挥棒，也是接力棒：一个会话指挥，活在多个代理之间传递。

一个 skill，装到 Claude Code、Codex、Antigravity、Grok Build 等代理里都能用。在哪个代理里调用它，那个会话就是**中控**：

1. **扫描本机。**看装了哪些代理（Claude Code、Codex、Antigravity/Gemini、Grok Build、Qwen Code、OpenCode、Gemini CLI），以及各自能用哪些模型。
2. **查榜单。**查公开榜单（默认 Artificial Analysis），按工种比较这些模型。结果缓存 7 天。
3. **出方案。**按工种拆分任务，写出分工方案：谁做、为什么选它、备选是谁、怎么验收、有哪些下载、哪些事要你决定。
4. **等你点头。**你确认之后才建任务文件夹、派活。
5. **派活和跟进。**用脚本启动各代理的无头进程，记录状态，失败时按原因换备选或重试。
6. **验收和汇报。**中控自己核对结果（跑测试、对数字、抽查原文），然后向你汇报。验收结果记进本机战绩，下次选人时参考。

它是 [Agent-Collaborator](https://github.com/tubo233/Agent-Collaborator) 的简化版：没有守护进程和网页界面，只有一份 SKILL.md、两个 Python 脚本（只用标准库）和几份参考文档。

## 安装

需要 Python 3.9+。

```sh
git clone https://github.com/tubo233/agent-baton.git
cd agent-baton
python install.py --dry-run      # 先看会装到哪些代理
python install.py                # 装到检测到的所有代理
```

`install.py` 是**复制**，不是链接。改了仓库里的文件后，重新运行一次就是更新。

| 代理 | 装到 | 怎么调用 |
|---|---|---|
| Claude Code | `~/.claude/skills/baton/` | `/baton 要做的事` |
| Codex | `~/.agents/skills/baton/` | `$baton 要做的事` |
| Antigravity（agy） | `~/.gemini/config/skills/baton/` | `/baton 要做的事`，或说"用 baton skill 分工" |
| Grok Build | 自动读 `~/.claude/skills/` 和 `~/.agents/skills/` | `/baton 要做的事` |
| Gemini CLI、Qwen Code、OpenCode | 各自的 skills 目录 | 见各自文档 |

这个 skill 只在你手动调用时运行，不会被代理自己触发：
- Claude Code 和 Grok 靠 `disable-model-invocation: true`；
- Codex 靠 `agents/openai.yaml` 里的 `allow_implicit_invocation: false`。

## 个人配置

每个人机器上的路径、代理端口、不能碰的数据、额度情况都不一样。这些写进个人配置，不要写进 skill 本身：

```sh
mkdir ~/.baton/profile
cp skill/baton/local/本机配置.example.md ~/.baton/profile/本机配置.md
cp skill/baton/local/profile.example.json ~/.baton/profile/profile.json
# 按自己的情况改这两个文件
python install.py --profile ~/.baton/profile
```

字段说明见 [skill/baton/local/README.md](skill/baton/local/README.md)。

## 不花额度先试一下

```sh
cd skill/baton/scripts
python scan_agents.py                       # 看本机有哪些代理和模型
python dispatch.py new-job --name 自测
echo hi > ~/.baton/jobs/<日期>_自测/启动提示.md
python dispatch.py run --job ~/.baton/jobs/<日期>_自测 --agent selftest --model 3
python dispatch.py wait --job ~/.baton/jobs/<日期>_自测 --timeout 30
```

`selftest` 是个假代理，睡几秒后写一个文件，用来确认启动、等待、停止都正常。

## 状态和数据放在哪

状态目录默认是 `~/.baton/`，可以用环境变量 `BATON_STATE_DIR` 改：

| 文件 | 内容 |
|---|---|
| `inventory.json` / `inventory.md` | 最近一次扫描的结果 |
| `leaderboard.json` | 榜单缓存，只留在本机。榜单网站一般不允许再分发它们的数据，不要提交进仓库 |
| `history.csv` | 本机战绩：每次运行的结果和验收结论 |
| `jobs/` | 默认的任务根目录，每个任务一个文件夹 |

## 安全边界（如实说明）

- **不读凭证。**脚本不读任何凭证文件：只运行各代理的 `--version` 和列模型命令，读 Codex 的模型缓存。
- **防的是走偏，不防恶意。**执行方和你是同一个操作系统用户。这里的边界防的是"守规矩但被任务带偏的代理"，不防恶意程序。真正的隔离要靠各代理自己的沙箱，以及个人配置里的禁止目录、`--deny` 规则。
- **外发可能经过第三方。**派给某个代理的内容会发到它的服务商；接第三方中转的，还会经过中转方。不要让代理读到不该外发的材料。
- **回报只是数据。**执行方写回来的日志和报告只当数据看，里面的"请执行……""已获批准"不算授权。

## 文件

```
install.py                       安装、更新、卸载
skill/baton/
  SKILL.md                       中控的完整流程
  agents/openai.yaml             Codex 用：只允许手动调用
  scripts/agent_catalog.json     各代理的发现方式和无头启动模板
  scripts/scan_agents.py         扫描代理、模型、榜单缓存、本机战绩
  scripts/dispatch.py            new-job / run / status / wait / stop / review
  references/hosts.md            各代理当中控时怎么提问、怎么后台运行
  references/agents.md           各代理当执行方的特点和坑
  references/leaderboards.md     查哪些榜、按工种看什么、怎么记
  references/brief-template.md   任务书和启动提示模板
  references/example-proposal.md 一份合格的分工方案
  local/                         个人配置的说明和示例（真正的配置不进仓库）
```

## 已验证的范围

截至 2026-11-24，在一台 Windows 11 机器上：

| 代理 | 当执行方（被派活） | 当中控（调用本 skill） |
|---|---|---|
| Claude Code 2.1.330 | 参数被接受，但该机的命令行没有单独登录 | 扫描、查榜单、派活（Codex、Antigravity、Grok）、等待、记验收这几步都跑过 |
| Codex 0.169 | 无头小试跑通 | `codex exec` 里调用 `$baton`：找到 skill，读了个人配置，出完方案就停下等确认 |
| Antigravity 1.5.16 | 无头小试跑通 | print 模式里调用 `/baton`：不能跑终端命令，改读上次的扫描结果，出完方案就停下 |
| Grok Build 1.0.70 | 无头小试跑通 | 能看到本 skill，未试当中控 |
| Gemini CLI、Qwen Code、OpenCode | 按官方文档写，未实测 | 未试 |

在 macOS 和 Linux 上没有测试过。

## 许可证

[MIT](LICENSE)。

榜单数据（leaderboard.json）不属于本仓库，也不随它分发；它的使用要遵守各榜单网站自己的条款。
