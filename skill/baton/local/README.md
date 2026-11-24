# local/：个人配置

这个文件夹放只属于你这台机器的配置。skill 的其他部分是通用的，可以分享；这里的东西不要分享。

有两个文件，都是可选的：

| 文件 | 给谁看 | 写什么 |
|---|---|---|
| `本机配置.md` | 中控（模型） | 用自然语言写的规则：目录约定、代理端口、数据边界、额度情况、分工偏好、本机踩过的坑 |
| `profile.json` | 脚本 | 机器能读的设置：任务根目录、各代理的路径和额外参数、禁止启动的目录 |

复制 `本机配置.example.md` 和 `profile.example.json`，去掉 `.example`，再按自己的情况改。

用 `install.py --profile <目录>` 安装时，会把那个目录里的这两个文件复制到每个代理的 skill 副本里。重新安装时，如果没给 `--profile`，已有的 `local/` 会保留。

## profile.json 字段

```json
{
  "job_root": "~/baton-jobs",
  "state_dir": "~/.baton",
  "forbidden_dirs": [
    {"path": "~/private-data", "tree": true}
  ],
  "agents": {
    "<代理 id>": {
      "path": "可执行文件的完整路径（自动找不到时才写）",
      "disabled": false,
      "notes": "停用原因等",
      "models": ["手工指定模型列表（覆盖自动扫描）"],
      "models_exclude": ["不想用的模型"],
      "models_file": "Codex 专用：模型目录文件（接第三方中转时用）",
      "extra_args": ["每次启动都追加的参数"],
      "forbidden_dirs": [{"path": "不许这个代理在里面启动的目录", "tree": true}],
      "env": {"变量名": "只给这个代理设的环境变量"}
    }
  }
}
```

**字段说明：**
- **`forbidden_dirs`：**`tree: true` 表示连子目录一起禁止，`false` 只禁这一个目录。`dispatch.py run` 会检查任务文件夹和 `--workdir`，命中就拒绝启动。
- **代理 id：**`claude`、`codex`、`agy`、`grok`、`gemini`、`qwen`、`opencode`，见 `scripts/agent_catalog.json`。
