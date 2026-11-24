# 查榜单：去哪查、看什么、怎么记

榜单用来回答一个问题：同一类活，本机这几个模型谁更强、谁更便宜、谁更快。它只是选人的依据之一，另外还要看本机战绩、额度，以及代理本身能不能跑命令、能不能联网。

## 什么时候查

- `scan_agents.py` 的输出里有"榜单缓存"一节：
  - 缓存不超过 7 天，且没有缺的模型：直接用，方案里写明日期；
  - 没有缓存、过期了，或者有本机新出现的模型：重查，至少把缺的补上。
- 用户说"不用查榜单"：跳过，方案里写"未使用榜单，按经验和本机战绩选"。

## 去哪查（按优先顺序）

1. **Artificial Analysis 模型榜**：https://artificialanalysis.ai/leaderboards/models
   - 综合智力指数（Intelligence Index），以及每个任务的花费和用时；覆盖的模型最多。
   - 页面上每个模型标了测试时用的思考档位（如 max、xhigh），记下来。
   - 指数换过版本，不同版本的分数不能直接比。记下页面上写的版本或日期。
   - 同站还有编码、代理、长上下文、幻觉率等单项，需要时按工种去查（见下节）。
2. **单项榜**，按工种补充：
   - 编码：SWE-bench Verified / Pro（https://www.swebench.com）、Terminal-Bench（https://www.tbench.ai）；
   - 网页和前端：LMArena WebDev（https://lmarena.ai）；
   - 通用对话偏好：LMArena Text。
3. **厂商发布页、可靠媒体的评测报道**：只在前两类查不到时用，标明"厂商自报"或"媒体报道"。

**怎么取：**
- 有联网搜索或网页读取工具的中控自己查。
- 没有的，把"查榜单"作为一个小工作项列进分工方案，交给能联网的代理（它只需要按本文件的格式交回一个 JSON）。

**不要用的来源：**
- 来路不明的"榜单汇总"博客。搜索结果里常见，数字互相矛盾，更新也慢。
- 只有截图、没有原始页面的说法。

## 按工种看什么

| 工种 | 主要指标 | 次要指标 |
|---|---|---|
| 写代码、跑实验、调试 | SWE-bench Pro / Verified、Terminal-Bench、编码指数 | 每任务花费 |
| 前端、网页 | LMArena WebDev | 编码指数 |
| 通读长文档、核对引用 | 长上下文推理（如 AA-LCR）、幻觉率（如 AA-Omniscience） | 智力指数 |
| 联网检索、取回 | 检索/浏览类基准（如 BrowseComp）、代理类指数 | 幻觉率 |
| 机械改动 | 每任务花费、用时 | 智力指数够用即可 |
| 判断、设计（中控自己做） | 智力指数 | —— |
| 交叉核对 | 和被核对方不同厂商 | 幻觉率 |

**几点提醒：**
- 榜单测的是模型本身。同一个模型装在不同代理里，工具和提示不同，表现会有差别。所以本机战绩（`history.csv`，由 `dispatch.py review` 记录）同样重要。
- 榜单上测的通常是最高档位，本机用的档位低时会打折扣。
- 厂商自报的分数和第三方复测常差很多，优先用第三方的。

## 怎么记：`<状态目录>/leaderboard.json`

```json
{
  "updated": "2026-11-24",
  "sources": [
    {"name": "Artificial Analysis 模型榜", "url": "https://artificialanalysis.ai/leaderboards/models",
     "checked": "2026-11-24", "note": "Intelligence Index v5"}
  ],
  "models": {
    "<榜单上的模型名>": {
      "local_ids": ["本机模型 ID 1", "本机模型 ID 2"],
      "vendor": "openai",
      "scores": [
        {"source": "Artificial Analysis 模型榜", "metric": "Intelligence Index", "value": 57,
         "effort_tested": "max", "date": "2026-11-24"},
        {"source": "Artificial Analysis 模型榜", "metric": "每任务花费（美元）", "value": 1.01, "date": "2026-11-24"}
      ],
      "notes": ""
    }
  },
  "checked_not_found": ["查过但榜单上没有的本机模型 ID"]
}
```

**填写规则：**
- **`local_ids`：**写本机 `scan_agents.py` 列出的模型 ID。扫描时就靠它判断哪些模型"缓存里没有"。
  - 一个榜单条目可以对应多个本机 ID，比如同一模型的不同档位、不同代理里的同一模型。
- **匹配要保守：**名字和版本号都对得上才算同一个模型。对不上的，不写进 `local_ids`，在方案里标"暂无榜单数据"。
- **`checked_not_found`：**查过、但榜单上确实没有的本机模型 ID 写在这里。这样缓存有效期内不会因为它们反复重查。
- **不编数字：**每个分数都要有 `source` 和 `date`。拿不到的就不写这一项。
- **只留在本机：**这个文件只存在状态目录里，不要放进要分享的 skill 或仓库。榜单网站的条款一般不允许再分发它们的数据。
