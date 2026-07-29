# MaiBot 私聊群事件日报

基于 [khiqwq/Maibot_daily_analysis](https://github.com/khiqwq/Maibot_daily_analysis)
改造的 MaiBot 1.0 / `maibot_sdk` 2.x 插件。它从明确配置的 QQ 群白名单读取聊天记录，
生成“这一天发生了什么”的事实型日报，并且只发到管理员 QQ 私聊；来源群全程静默。

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

不生成成员活跃度、MBTI、群友画像、金句排行或“压抑指数”等娱乐分析。

## 官方安装流程

1. 将完整插件目录放入 MaiBot 的插件目录。
2. 在 MaiBot WebUI 的插件管理中加载/重载插件。
3. 在 WebUI 中填写配置并启用插件。

Docker 部署时通常将本目录放在宿主机已映射到 MaiBot 插件目录的位置。插件只声明
`jinja2>=3.1.0` 一个 Python 依赖，MaiBot 会根据 `_manifest.json` 处理依赖。

本插件不需要执行额外安装脚本，也不要单独安装 Playwright 或 Chromium。

## 必填配置

推荐直接在 MaiBot WebUI 填写。对应的 TOML 结构如下：

```toml
[plugin]
enabled = true
config_version = "3.0.0"

[summary]
max_events = 8
anchors_per_event = 2
events_per_page = 4
max_input_messages = 1200
include_anchor_quotes = true
include_links = true

[auto_summary]
enabled = true
time = "00:10"
timezone = "Asia/Shanghai"
min_messages = 10
# 所有自动和手动总结共同使用的来源群白名单；留空时全部禁止
target_chats = ["123456789", "987654321"]
# 自动日报唯一接收 QQ；留空时自动任务不生成
recipient_user = "111111111"

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

关键约束：

- `auto_summary.target_chats` 必须明确填写。空列表不是“全部”，而是完全禁用群总结。
- `auto_summary.recipient_user` 必须同时在 `command_permission.admin_users` 中。
- 多个管理员只共享来源群白名单，不共享手动请求结果。
- QQ 号和群号在配置中按字符串填写，避免数字类型转换。
- `advanced.model_task` 必须是 MaiBot 中存在的任务名，例如 `utils`；不能填供应商模型名。

保存配置后，插件会通过官方配置热更新生命周期重启定时调度器。

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

## 许可与来源

本项目沿用上游 GPL-3.0-or-later 许可。原项目作者与仓库信息保留在 `_manifest.json`。
