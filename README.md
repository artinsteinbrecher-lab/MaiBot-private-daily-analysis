# MaiBot 私聊群事件日报

维护仓库：[artinsteinbrecher-lab/MaiBot-private-daily-analysis](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis)

基于 [khiqwq/Maibot_daily_analysis](https://github.com/khiqwq/Maibot_daily_analysis) 改造的
MaiBot 1.x / `maibot_sdk` 2.x 插件。它读取明确配置的 QQ 群白名单，生成带时间戳和回查锚点的
事实型群事件日报，并且只将进度、结果和错误发送到管理员 QQ 私聊。

本插件不再承担群聊静默。需要绝对静默时，请独立安装“QQ 群绝对静默守卫”插件。
拆分后，静默规则与日报的模型调用、数据库读取、图片渲染和启停状态互不依赖。

## 主要能力

- 管理员私聊执行 `/summary 群号 [今天|昨天]`，或 `/summary 全部 [今天|昨天]`。
- 次日定时总结前一个完整自然日，默认于配置时区的 00:10 执行。
- 结果只发给发起请求的管理员；自动日报只发给显式配置的唯一接收 QQ。
- “完整覆盖”按分片分析全部有效文本；“均衡抽样”适合低成本快速日报。
- 分片失败会先重试，再按时间顺序二分重试；仍失败时不会伪装成完整结果。
- 日报分为“主要事件”和“其他动态”，并展示覆盖率、成功/失败分片与缺失时间范围。
- 每个事件包含时间范围、经过、结论、待确认事项、必要参与者、链接和回查原话。
- 时间、参与者、链接和引用会与源消息再次核对，不能落到原记录的字段会被移除。
- 保留上游 `/mysummary` 个人总结，可独立关闭。

插件不会生成活跃度、MBTI、群友画像、金句排行或情绪指数等娱乐分析。

## 兼容范围

- MaiBot：`1.0.0`—`1.99.99`
- `maibot_sdk`：`2.0.0`—`2.99.99`
- 主要环境：Ubuntu + Docker
- 已验证：MaiCore 1.1.0、Python 3.13、NapCat 正向 WebSocket

插件不直连 NapCat，也不保存模型 URL、API Key 或供应商配置。消息读取、模型调用、渲染和发送
均通过 MaiBot 官方 `ctx.*` 能力完成。

## 官方安装方式

1. 将完整插件目录放入 MaiBot 的插件目录。
2. 在 MaiBot WebUI 的“插件管理”中加载插件。
3. 点击插件设置，在 WebUI 中填写配置。
4. 保存后启用插件；配置更新由 MaiBot 的官方配置生命周期处理。

发布目录至少应包含：

```text
plugin.py
_manifest.json
core/
templates/
fonts/
```

插件只声明 `jinja2>=3.1.0`，不需要单独安装 Playwright 或 Chromium。不要提交真实
`config.toml`；仓库的 `config.example.toml` 只包含示例账号。

## 推荐配置

以下配置适合高流量群的完整日报。所有 QQ 号和群号都应使用字符串。

```toml
[plugin]
enabled = true
config_version = "3.2.0"

[summary]
coverage_mode = "完整覆盖"
detail_level = "完整"
max_events = 12
max_minor_events = 30
anchors_per_event = 2
events_per_page = 4
# 仅用于“均衡抽样”；“完整覆盖”不使用此上限。
max_input_messages = 1200
include_anchor_quotes = true
include_links = true

[user_summary]
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
target_chats = ["123456789", "987654321"]
recipient_user = "111111111"

[command_permission]
admin_users = ["111111111"]

[advanced]
# 这是 MaiBot 模型任务名，不是模型名、URL 或供应商名。
model_task = "utils"
inject_memory = false
llm_timeout_seconds = 60
render_timeout_seconds = 25
group_timeout_seconds = 900
event_chunk_messages = 120
event_chunk_characters = 8000
event_retry_count = 2
split_chunk_on_failure = true
```

关键约束：

- `auto_summary.target_chats` 为空表示禁用所有群总结，不表示“全部群”。
- `recipient_user` 必须同时存在于 `command_permission.admin_users`。
- 未配置有效自动接收人时，自动任务不会读取群消息，也不会调用模型。
- 多个管理员共用来源群白名单，但每次手动请求的进度与结果只发给发起账号。
- `advanced.model_task` 必须是 MaiBot 中已存在的任务，例如 `utils`。
- 没加入的群没有可用群聊流，会被标为空或不可用，不会阻塞其他群。

### 模型来源

插件沿用 MaiBot 当前模型分配，不新增模型配置：

```text
日报插件 → advanced.model_task（默认 utils）→ MaiBot 任务中的模型列表
```

因此升级插件不会修改模型列表、模型供应商、URL、Key、温度或路由策略。

## 覆盖模式与完整性

### 完整覆盖

对统计时段内全部有效文本消息按消息数和字符数双重分片。每个失败分片最多重试
`event_retry_count` 次；仍失败且允许拆分时，再将该分片按时间顺序二分，各自重新处理一次。

完整覆盖使用确定性的本地候选合并，避免额外一次大模型合并调用成为新的超时点。
“覆盖率 100%”表示全部有效文本都进入了成功分片，不表示图片会逐条复述全部聊天。

### 均衡抽样

最多取 `max_input_messages` 条，保留首尾消息并覆盖全天时段。适合低成本、快速查看；
输出会明确标为抽样，不能当作完整记录。

### 失败表达

只要存在无法恢复的分片，结果状态就是“部分完成”，并列出：

- 总消息数与成功分析消息数；
- 覆盖百分比；
- 成功、失败、重试及二分次数；
- 未覆盖分片的起止时间。

插件不会静默丢弃失败分片，也不会把部分结果标记为完整结果。

## 输出结构

每次请求按私聊顺序发送：

1. 开始和逐群进度；
2. 可搜索的纯文本总目录；
3. 每个群的“主要事件”分页图片；
4. 每个群的“其他动态”分页图片；
5. 成功、部分完成、空内容和失败计数。

主要事件最多 `max_events` 条，其他动态最多 `max_minor_events` 条；两类限额互不挤占。
每页事件数由 `events_per_page` 控制。

## 私聊命令

只有 `admin_users` 中的账号可以私聊执行：

```text
/summary 123456789 今天
/summary 123456789 昨天
/summary 全部 今天
/summary 全部 昨天
```

省略日期时默认为今天。今天指配置时区当天 00:00 到请求时刻；昨天指前一自然日。
群内发送 `/summary` 不会生成日报，也不会发送任何状态到群里。

## 自动日报

定时任务总结前一自然日 `[00:00, 次日 00:00)`。执行前会校验：

1. 自动任务已启用；
2. 接收 QQ 已填写且位于管理员名单；
3. 来源群白名单非空；
4. 群聊流可用且时段内消息数达到阈值。

不满足安全前置条件时只写插件日志，不读取群消息、不调用模型。

## 独立绝对静默

日报 v3.2 已移除 `[silence]` 配置和发送前 Hook。要让指定群绝对静默，请使用独立的
“QQ 群绝对静默守卫”，并在守卫的群名单中填写目标群号。这样：

- 守卫只负责阻止名单内群聊的出站消息；
- 日报仍可读取已入库消息并私聊发送结果；
- 停用日报不会解除静默；
- 停用守卫不会改变日报的来源群、模型或输出配置；
- 管理员私聊和名单外群聊不受影响。

## Ubuntu / Docker

- 时区由 Python `zoneinfo` 与 `auto_summary.timezone` 决定，不依赖容器系统时区。
- 模板优先使用 Noto Sans CJK SC、Noto Sans SC、WenQuanYi Micro Hei。
- 渲染使用宿主 `render.html2png` 且 `allow_network=false`，模板不访问外网。
- 单群默认超时 900 秒；多个群的失败状态相互隔离。
- 模型调用由全局信号量限制为最多 2 个并发，防止高流量日报瞬间压垮模型任务。

## 本地检查

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile plugin.py core/analysis.py core/rendering.py core/event_digest.py
```

完整运行仍应在 MaiBot 宿主中验证插件加载、配置、消息读取、模型调用、渲染和 QQ 私聊发送。

## 故障排查

### 私聊命令没有回应

检查管理员私聊是否进入 MaiBot、账号是否在 `admin_users`、私聊会话是否能打开，以及
`advanced.model_task` 是否存在。绝对静默守卫只匹配群聊，不应匹配管理员私聊。

### 配置了群但没有日报

确认机器人已入群、MaiBot 已收到该群消息、群号以字符串形式位于 `target_chats`，
且统计时段消息数达到 `min_messages`。可先私聊执行 `/summary 群号 今天`。

### 报告显示部分完成

查看覆盖横幅中的失败时间范围，再在 MaiBot 日志中检查对应分片的模型超时或 HTTP 错误。
插件已经完成重试和可选二分；无需通过增大事件数量来掩盖缺失。

### 图片中文为方块

在 MaiBot/渲染环境安装 Noto CJK 或文泉驿中文字体，然后重新加载插件。

## 隐私与安全

- 群聊内容会提交给 `advanced.model_task` 对应的模型供应商。
- 日报只发给配置的管理员私聊，但管理员应自行保护生成内容。
- 插件不读取服务器密码、NapCat Token、WebUI Token 或模型 API Key。
- 使用前应遵守群成员知情、平台规则和适用的数据保护要求。

## 许可与来源

本项目沿用上游 GPL-3.0-or-later 许可，保留原作者与上游仓库信息。
