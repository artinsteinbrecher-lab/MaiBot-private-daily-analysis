# 麦麦认真写书 · v3.6.0 标准版

维护仓库：[artinsteinbrecher-lab/MaiBot-private-daily-analysis](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis)

当前安装包：`khiqwq_daily_analysis-v3.6.0-standard.zip` ·
[查看正式 Release](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/tag/v3.6.0) ·
[返回版本与下载](../../docs/DOWNLOADS.md)

这个版本专心把群聊里的事情写清楚。它会同时保留主要事件和普通事件，把日报只发给管理员私聊，并尽量核对时间、参与者、链接和引用。

你不需要修改 MaiBot，也不需要另外配置四个模型任务。插件会使用 MaiBot 当前的默认回复模型。

本插件不再承担群聊静默。需要让麦麦在指定群里保持安静时，请搭配“麦麦群安静插件”。
拆分后，静默规则与日报的模型调用、数据库读取、图片渲染和启停状态互不依赖。

## 它能做什么

- 管理员私聊执行 `/summary 群号 [今天|昨天]`，或 `/summary 全部 [今天|昨天]`。
- 次日定时总结前一个完整自然日，默认于配置时区的 00:10 执行。
- 结果只发给发起请求的管理员；自动日报只发给显式配置的唯一接收 QQ。
- “完整覆盖”按分片分析全部有效文本；“均衡抽样”适合低成本快速日报。
- 分片失败会先重试，再按时间顺序二分重试；仍失败时不会伪装成完整结果。
- 全量分片先生成轻量话题索引，本地合并后再只精炼最多 12 个主要事件。
- 主要事件详细记录经过、结论、待确认事项、参与者、链接和回查原话。
- 普通话题不占主要事件名额，按小时输出“时间戳 + 标题 + 一句话”的私聊文本时间线。
- 时间、参与者、链接和引用会与源消息再次核对，不能落到原记录的字段会被移除。
- 保留上游 `/mysummary` 个人总结，可独立关闭。

插件不会生成活跃度、MBTI、娱乐化群友画像、金句排行或情绪指数；`/mysummary` 保留的是有消息证据支持的事实型个人画像。

## 可以装在哪里

- MaiBot：`1.0.0`—`1.99.99`
- `maibot_sdk`：`2.0.0`—`2.99.99`
- 主要环境：Ubuntu + Docker
- 已验证：MaiCore 1.1.0、Python 3.13、NapCat 正向 WebSocket

插件不直连 NapCat，也不保存模型 URL、API Key 或供应商配置。消息读取、模型调用、渲染和发送
均通过 MaiBot 官方 `ctx.*` 能力完成。

## 五分钟安装

1. 解压 standard ZIP，保留其中唯一的顶层插件目录。
2. 将该目录放入 MaiBot 插件目录。Docker 常见宿主路径为 `./data/MaiMBot/plugins/`，并确认 `plugin.py` 直接位于：

   ```text
   ./data/MaiMBot/plugins/<插件目录>/plugin.py
   ```

   如果出现 `<插件目录>/<插件目录>/plugin.py`，说明多套了一层目录。
3. 升级已有安装时先备份原目录，替换程序文件但保留真实 `config.toml` 和独立插件数据目录。
4. 在 MaiBot WebUI 的“插件管理”中加载插件，在插件设置中至少填写来源群 `target_chats`、接收账号 `recipient_user` 和包含该账号的 `admin_users`。
5. 保存并启用，确认日志出现“私聊群事件日报插件已加载”，再由管理员私聊执行 `/summary 群号 今天`。

开始通知、处理进度、报告与完成通知都进入管理员私聊，即表示安装成功。首次测试的群需要已经有当天消息，并达到 `min_messages`。

仅执行以上步骤即可使用完整的群聊日报功能。默认情况下，四个需要 LLM 的流程都使用
MaiBot 的 `replyer` 任务，不要求修改 MaiBot 主程序。

普通版不包含宿主补丁，也不会在 MaiBot 高级模型任务页面新增插件专用任务。
不要执行 `extras/` 安装脚本；standard ZIP 本身也不包含该目录。

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
config_version = "3.6.0"

[summary]
coverage_mode = "完整覆盖"
detail_level = "完整"
max_events = 12
# 0 表示普通话题不设用户上限，仍有 500 条安全上限。
max_minor_events = 0
refine_major_events = true
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
# 四个内部流程统一跟随 MaiBot 的 replyer；供应商与模型 ID 由 replyer 管理。
inject_memory = false
llm_timeout_seconds = 180
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
- 插件设置中不再出现模型任务、供应商或模型 ID 字段。
- 四个内部流程固定跟随 `replyer`，下载插件本身即可运行。
- 如需四任务独立模型分配，请改用“麦麦一起写书”（v3.6.0 多模型版）。
- 没加入的群没有可用群聊流，会被标为空或不可用，不会阻塞其他群。

### 模型来源

普通版不新增供应商、模型 ID 或 MaiBot 全局模型任务。插件内部仍按下列职责分阶段执行，但四个阶段都调用 `replyer`：

```text
事实、事件、证据分片提取 → plugin_daily_extract
主要事件精炼、压缩、去重 → plugin_daily_compose
独立事实复核（只能删声明或降级状态）→ plugin_daily_verify
事实型个人画像 → plugin_user_profile
```

这四个名称只是插件内部的职责标识，不会出现在普通版的 MaiBot 高级任务页面。
升级插件不会修改 `replyer` 的模型列表、供应商、URL、Key 或模型 ID。

## 覆盖模式与完整性

### 完整覆盖

对统计时段内全部有效文本消息按消息数和字符数双重分片。每个失败分片最多重试
`event_retry_count` 次；仍失败且允许拆分时，再将该分片按时间顺序二分，各自重新处理一次。

完整覆盖使用确定性的本地候选合并，避免额外一次大模型合并调用成为新的超时点。
“覆盖率 100%”表示全部有效文本都进入了成功分片，不表示图片会逐条复述全部聊天。

### 四阶段事实处理

1. `plugin_daily_extract` 按分片提取事实、事件、证据 ID 和回查锚点；
2. 插件在本地验证证据 ID、声明原文、时间、参与者、链接和引用，并合并相邻同类话题；
3. `plugin_daily_compose` 只对最终最多 `max_events` 个主要事件精炼、压缩和去重，不得增加事实；
4. `plugin_daily_verify` 独立复核主要事件，只能删除不受支持的原声明或下调事件状态。

精炼或复核失败时保留已经通过本地严格证据约束的结果，不会让整个群日报失败，也不会把
复核模型的新声明写回报告。

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
4. 每个群全部普通话题的按小时时间戳分页文本（每页重复群名和页码）；
5. 成功、部分完成、空内容和失败计数。

主要事件最多 `max_events` 条，每页数量由 `events_per_page` 控制。普通话题不占主要事件名额；
`max_minor_events = 0` 表示不设用户上限，插件内部仍保留 500 条安全上限，防止异常模型输出
造成私聊刷屏。普通话题只保留简略时间线，不渲染成事件卡片。主要事件图片渲染或发送回执异常时，插件会自动补发可搜索文字版，并把该群标记为“部分完成”而不是整群失败。

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

日报从 v3.2 起已移除 `[silence]` 配置和发送前 Hook。要让指定群绝对静默，请使用独立的
“麦麦群安静插件”，并在安静名单中填写目标群号。这样：

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

检查管理员私聊是否进入 MaiBot、账号是否在 `admin_users`、私聊会话是否能打开。
普通版的四个内部流程固定跟随 `replyer`；请确认 MaiBot 的 `replyer` 已配置可用模型。
麦麦群安静插件只匹配群聊，不应匹配管理员私聊。

### 配置了群但没有日报

确认机器人已入群、MaiBot 已收到该群消息、群号以字符串形式位于 `target_chats`，
且统计时段消息数达到 `min_messages`。可先私聊执行 `/summary 群号 今天`。

### 报告显示部分完成

查看覆盖横幅中的失败时间范围，再在 MaiBot 日志中检查对应分片的模型超时或 HTTP 错误。
插件已经完成重试和可选二分；无需通过增大事件数量来掩盖缺失。

### 图片中文为方块

在 MaiBot/渲染环境安装 Noto CJK 或文泉驿中文字体，然后重新加载插件。

## 隐私与安全

- 群聊内容会提交给 MaiBot 中实际承接四个插件任务（或回退任务）的模型供应商。
- 日报只发给配置的管理员私聊，但管理员应自行保护生成内容。
- 插件不读取服务器密码、NapCat Token、WebUI Token 或模型 API Key。
- 使用前应遵守群成员知情、平台规则和适用的数据保护要求。

## 许可与来源

本项目沿用上游 GPL-3.0-or-later 许可，保留原作者与上游仓库信息。
