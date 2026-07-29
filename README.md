# MaiBot 私聊群事件日报

维护仓库：[artinsteinbrecher-lab/MaiBot-private-daily-analysis](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis)

基于 [khiqwq/Maibot_daily_analysis](https://github.com/khiqwq/Maibot_daily_analysis)
改造的 MaiBot 1.0 / `maibot_sdk` 2.x 插件。它从明确配置的 QQ 群白名单读取聊天记录，
生成“这一天发生了什么”的事实型日报，并且只发到管理员 QQ 私聊；来源群全程静默。

可选开启“绝对静默”出站保护。开启后，指定来源群仍会正常接收和保存消息，
但普通回复、@ 回复、昵称触发、命令回复和其他插件输出都会在 Platform IO 前被拦截。

插件主要面向 Ubuntu + Docker 部署。运行能力全部通过 MaiBot 官方 `ctx.*` API 调用，
图片由宿主的 `render.html2png` 生成，不自带浏览器、不写临时图片文件。

## 功能

- 私聊手动总结一个白名单群，或一次总结全部白名单群。
- 次日定时总结前一个完整自然日（默认 00:10 执行）。
- 自动任务只有一个单独配置的接收 QQ；未配置或不在管理员列表时不读取消息、不生成。
- 多个管理员可分别发起手动请求；进度、目录、图片和失败状态只返回给请求人。
- 来源群不发送确认、进度、结果或错误消息。
- 先发送一份可搜索的纯文本总目录，再按群发送自动分页的事件时间线图片。
- 无重要事件的群只出现在目录中，不生成图片。
- 每个事件包含时间范围、事情经过、结论、待确认事项、必要参与者、重要链接和精确回查原话。
- 模型输出的时间、回查原话、参与者和链接会再次与输入消息核对；无法落到原记录的字段会被移除。
- 保留原插件的 `/mysummary` 个人总结功能，便于已有用户继续使用。

群事件日报不生成成员活跃度、MBTI、群友画像、金句排行或“压抑指数”等娱乐分析。
兼容保留的 `/mysummary` 是独立功能，仍可按 `[user_summary]` 配置生成个人总结；不需要时可直接关闭。

## 兼容范围

- MaiBot：`1.0.0`—`1.99.99`
- `maibot_sdk`：`2.0.0`—`2.99.99`
- 主要部署环境：Ubuntu + Docker
- 已验证运行环境：MaiCore 1.1.0、Python 3.13、NapCat 正向 WebSocket

插件不直接连接 NapCat，也不保存模型供应商的 API Key。消息读取、模型调用、渲染和发送均由
MaiBot 宿主提供的官方能力完成。

## 官方安装流程

1. 将完整插件目录放入 MaiBot 的插件目录。
2. 在 MaiBot WebUI 的插件管理中加载/重载插件。
3. 在 WebUI 中填写配置并启用插件。

Docker 部署时通常将本目录放在宿主机已映射到 MaiBot 插件目录的位置。插件只声明
`jinja2>=3.1.0` 一个 Python 依赖，MaiBot 会根据 `_manifest.json` 处理依赖。

本插件不需要执行额外安装脚本，也不要单独安装 Playwright 或 Chromium。

发布包必须至少包含：

```text
plugin.py
_manifest.json
core/
templates/
fonts/
```

`tests/`、`README.md`、`CHANGELOG.md` 和 `config.example.toml` 不参与运行，但建议一并保留。
不要上传真实的 `config.toml`；仓库已通过 `.gitignore` 排除它。

## 必填配置

推荐直接在 MaiBot WebUI 填写。对应的 TOML 结构如下：

```toml
[plugin]
enabled = true
config_version = "3.1.0"

[summary]
max_events = 8
anchors_per_event = 2
events_per_page = 4
max_input_messages = 1200
include_anchor_quotes = true
include_links = true

[user_summary]
# 这是兼容保留的 /mysummary，与群事件日报相互独立
enabled = false
view_others_mode = "白名单"
allowed_users = []
slot_1 = "3H活跃轨迹"
slot_2 = "群友画像+炫压抑评级(并排)"
slot_3 = "语出惊人"
slot_4 = "无"

[auto_summary]
enabled = true
time = "00:10"
timezone = "Asia/Shanghai"
min_messages = 10
# 所有自动和手动总结共同使用的来源群白名单；留空时全部禁止
target_chats = ["123456789", "987654321"]
# 自动日报唯一接收 QQ；留空时自动任务不生成
recipient_user = "111111111"

[silence]
# 可选：在真正发送到 QQ 前拦截指定来源群的所有出站消息
enabled = false
# 只有同时位于 auto_summary.target_chats 的群号才生效
target_chats = ["123456789"]

[command_permission]
# 可以私聊执行 /summary 的 QQ。自动接收 QQ 也必须在这里。
admin_users = ["111111111", "222222222"]

[advanced]
# 填 MaiBot 的模型任务名，不是具体模型名
model_task = "utils"
inject_memory = false
llm_timeout_seconds = 60
render_timeout_seconds = 25
group_timeout_seconds = 300
```

仓库中的 [`config.example.toml`](config.example.toml) 提供了不含真实 QQ 号的完整示例。

关键约束：

- `auto_summary.target_chats` 必须明确填写。空列表不是“全部”，而是完全禁用群总结。
- `auto_summary.recipient_user` 必须同时在 `command_permission.admin_users` 中。
- `silence.target_chats` 只有在 `silence.enabled = true` 且群号同时位于来源群白名单时生效。
- 绝对静默仅拦截出站消息，不影响该群消息接收、入库和日报读取。
- 多个管理员只共享来源群白名单，不共享手动请求结果。
- QQ 号和群号在配置中按字符串填写，避免数字类型转换。
- `advanced.model_task` 必须是 MaiBot 中存在的任务名，例如 `utils`；不能填供应商模型名。

保存配置后，插件会通过官方配置热更新生命周期重启定时调度器。

### 模型从哪里来

插件不会要求你额外填写模型 URL、API Key 或具体模型名。它调用
`advanced.model_task` 指向的 MaiBot 模型任务，默认是 `utils`：

```text
插件 → MaiBot 的 utils 任务 → 该任务 model_list 中的模型 → 已配置的模型供应商
```

因此，日报使用哪个模型取决于 MaiBot 的模型任务分配。如果该任务里的所有模型都返回
HTTP 429/500/503，手动请求会把失败状态发给请求管理员，自动任务会记录错误；来源群仍然保持静默。

## 私聊命令

以下命令只能由 `admin_users` 中的账号私聊机器人发送：

```text
/summary 123456789 今天
/summary 123456789 昨天
/summary 全部 今天
/summary 全部 昨天
```

省略日期时默认为“今天”：

```text
/summary 123456789
```

“今天”表示配置时区当天 00:00 到请求时刻；“昨天”表示昨天 00:00 到今天 00:00。
每次手动请求都重新读取记录并生成，不复用其他管理员或其他时刻的结果。

如果有人在群聊中发送 `/summary`，插件只拦截命令，不调用任何发送能力。

`/mysummary` 的参数和权限逻辑沿用上游插件。关闭 `[user_summary].enabled` 后该命令不可用。

## 自动日报

默认在配置时区次日 `00:10` 执行，统计范围严格为：

```text
前一天 00:00（含）— 当天 00:00（不含）
```

生成前依次校验：

1. 自动功能已启用；
2. `recipient_user` 已填写；
3. 接收 QQ 位于 `admin_users`；
4. 来源群白名单非空；
5. 至少一个白名单群已经形成可用群聊流。

任何一项不满足时只写插件日志，不读取来源群消息，也不向群内发送内容。

如果白名单中配置了机器人尚未加入的群，该群不会形成可用聊天流，也没有可读取的历史记录。
它会被标记为空或不可用，不会阻塞其他已加入群的总结。

## 输出格式

每次请求先收到：

1. 私聊进度；
2. 纯文本日报目录，列出所有来源群和每个重要事件的开始时间；
3. 每个有重要事件的群对应的一组时间线图片；
4. 成功、空内容和失败数量。

详情图片按 `events_per_page` 自动分页。默认每群最多 8 个主要事件、每个事件最多
2 条精确原话。超过 `max_input_messages` 的高流量群会按全天时序均衡抽样，并在图片中标明。

## Ubuntu / Docker

- 时区使用 Python 标准库 `zoneinfo` 和 `auto_summary.timezone`，不依赖容器系统时区。
- 模板字体优先使用 `Noto Sans CJK SC`、`Noto Sans SC`、`WenQuanYi Micro Hei`。
- 若中文显示为方块，请在 MaiBot/渲染容器安装 Noto CJK 或文泉驿中文字体。
- 事件日报模板不访问外部网络；渲染使用宿主能力且 `allow_network=false`。
- 单群整体处理默认超时 300 秒，某个群失败不会把状态发回来源群。

## 绝对静默的边界

启用前请确认你理解它的作用范围：

- 只检查 `chat_type="group"` 的出站消息，不匹配任何 QQ 私聊。
- 只对同时存在于 `auto_summary.target_chats` 与 `silence.target_chats` 的群生效。
- 在 Platform IO 真正发送前拦截，因此普通回复、@ 回复、昵称触发、命令回复以及其他插件
  向该群发送的内容都会被阻止。
- 不阻止群消息进入 NapCat、MaiBot 和数据库，也不影响日报读取这些消息。
- 不修改其他群的回复频率，不影响名单外群聊和管理员私聊。
- 配置错误时建议先关闭 `silence.enabled`，确认群号后再重新开启。

这不是“把回复频率调到最低”的概率性静默，而是发送前的确定性保护。若你仍需要机器人在该群
响应某些命令，不要对该群开启绝对静默。

## 使用的官方能力

`_manifest.json` 声明以下宿主能力：

- `message.get_by_time_in_chat`
- `chat.get_group_streams`
- `chat.open_session`
- `llm.generate`
- `llm.get_available_models`
- `render.html2png`
- `send.text`
- `send.image`
- `config.get`
- `maisaka.context.append`

自动私聊通过 `chat.open_session(platform="qq", chat_type="private", user_id=...)`
获取目标聊天流，不拼接或猜测私聊 stream ID。

## 本地检查

纯数据和事件溯源测试不依赖 MaiBot SDK：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile plugin.py core/analysis.py core/rendering.py core/event_digest.py
```

完整运行仍应在 MaiBot 宿主中验证插件加载、WebUI 配置、模型调用、图片渲染和 QQ 私聊发送。

## 故障排查

### 私聊命令没有回复

依次查看 MaiBot 日志：

1. 是否看到管理员私聊消息进入；
2. 是否通过 `admin_users` 校验；
3. 是否成功打开管理员私聊会话；
4. `advanced.model_task` 是否存在且至少有一个可用模型；
5. 模型供应商是否返回 401、429、500 或 503。

绝对静默只匹配群聊，不会静默管理员私聊。

### 群被配置了但没有日报

- 确认机器人已经加入该群；
- 确认 MaiBot 已经收到该群消息并形成群聊流；
- 确认群号以字符串形式同时填写在正确名单中；
- 确认统计时段内消息数达到 `min_messages`；
- 手动执行 `/summary 群号 今天`，从私聊状态定位读取、模型或渲染环节。

### 图片中文显示为方块

为 MaiBot/渲染环境安装 Noto CJK 或文泉驿中文字体，然后重新加载插件。插件不会联网下载字体。

### 模型调用失败

插件使用的是 MaiBot 模型任务，不是独立模型配置。先在 MaiBot 日志确认实际选择的模型及上游
HTTP 错误，再处理对应供应商渠道；不要把模型 URL 填进 `advanced.model_task`。

## 隐私与安全

- 群聊内容会被提交给 `advanced.model_task` 对应的模型供应商进行总结。
- 日报只发送给配置的管理员私聊，但管理员应自行保护 QQ 账号和生成内容。
- `config.toml` 可能包含 QQ 号、群号等标识信息，默认不纳入 Git。
- 插件不会读取服务器密码、NapCat Token、MaiBot WebUI Token 或模型 API Key。
- 开启实验性的 `inject_memory` 后，总结可能进入 MaiBot 会话上下文；不需要时保持关闭。
- 使用前应遵守群聊成员知情、平台规则和所在地的数据保护要求。

## 许可与来源

本项目沿用上游 GPL-3.0-or-later 许可，基于
[khiqwq/Maibot_daily_analysis](https://github.com/khiqwq/Maibot_daily_analysis)
改造。原作者、许可证与上游仓库信息予以保留；本分支的具体改动见 [`CHANGELOG.md`](CHANGELOG.md)。
