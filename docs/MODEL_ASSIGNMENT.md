# 多模型任务分配建议

评估日期：2026-08-02。目标优先级为：真实性 > 总结效果 > 稳定性 > 耗时与成本。
本文件只给出接入后的初始路由，不自动写入任何供应商、URL、API Key 或模型 ID。

## 先区分官方名称与供应商别名

用户提供的 `gpt5.6luna`、`dsv4f0731`、`gemini3.1pro`、`gemini3.5f`、`grok4.5`
应先视为供应商别名。只有供应商元数据、官方模型返回信息或同源测试能够证明映射时，才能把它们
等同于官方模型名称。`dsv4f0731` 与 DeepSeek 官方列出的模型版本 `DeepSeek-V4-Flash-0731` 高度吻合，
但官方 API ID 是 `deepseek-v4-flash`，不是该别名；仍需用供应商元数据确认实际映射，不能只凭字符串认定。

## 接入后的初始分配

| MaiBot 插件专用任务 | 初始首选 | 选择理由与边界 |
|---|---|---|
| `plugin_daily_extract` | GPT-5.6 Luna | 负责分片提取事件、参与者、时间、链接和证据 ID。当前先用已经测通的 Luna，避免 DS 在 Gemini 兼容层中长 JSON 截断。 |
| `plugin_daily_compose` | GPT-5.6 Luna | 只整理已经绑定证据的候选事件，负责压缩、去重和排版，不得新增事实；替代原来的 Gemini 3.5 Flash。 |
| `plugin_daily_verify` | Gemini 3.1 Pro | 保持为总审模型，独立复核主要事件，只能删除没有证据支持的声明或降低事件状态。 |
| `plugin_user_profile` | GPT-5.6 Luna | 只总结可观察的话题、表达特点、样本时段与真实原话，不生成性格测验、心理诊断或娱乐称号。 |

如果完全不填，四项都由 MaiBot 回退到 `replyer`。插件不会再自行写死输出 token 上限，
每个任务的 `max_tokens`、超时和温度都由 MaiBot 的高级任务配置负责。

## 暂不固定的候选

- `dsv4f0731`：本版不再放入日报或画像路由。此前真实长日报中它在 Gemini 兼容层反复达到 `MAX_TOKENS` 并返回空内容，保留为以后单独验证的候选。
- Gemini 3.5 Flash：保留为速度对照组。官方模型卡的 128K 多目标召回结果低于 Gemini 3.1 Pro，真实性优先时不作为首选提取模型。
- 不要把多个候选同时放进采用随机选择策略的任务池来做 A/B；每轮只配置一个模型，确保结果可归因。

## 同源 A/B 验收

固定同一天、同一群、同一份原始消息、相同分片、相同提示词和相同输出上限。先比较：

1. 主要事件召回率与普通话题覆盖率；
2. 每条事实、结论、待办是否能落回真实消息 ID；
3. 时间、参与者、链接和原话是否正确；
4. 不同话题错合并率、同一话题错拆分率；
5. JSON 解析成功率、失败重试和二分恢复次数；
6. 复核删除的无依据声明数，以及错误删除的真实声明数。

成本与总耗时只作为次要指标。至少用三个具有人工事实清单的真实群聊日样本后再固定正式路由。

## 官方资料入口

- OpenAI GPT-5.6 model guidance：`https://developers.openai.com/api/docs/guides/model-guidance?model=gpt-5.6`
- Gemini 3.1 Pro 模型卡：`https://deepmind.google/models/model-cards/gemini-3-1-pro/`
- Gemini 3.5 Flash 模型卡：`https://deepmind.google/models/model-cards/gemini-3-5-flash/`
- xAI Grok 4.5 公告：`https://x.ai/news/grok-4-5`
- DeepSeek 官方模型列表：`https://api-docs.deepseek.com/quick_start/pricing`
