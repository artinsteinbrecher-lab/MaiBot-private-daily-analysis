# 日报插件模型路由评估（2026-08-01）

> 历史评估说明：本文记录 v3.4 两任务阶段的 2026-08-01 方案，已被本地 `../MODEL_ASSIGNMENT.md` 的 v3.6.0 四任务方案取代，不应作为当前安装指引。

## 结论

质量优先时，不再把 Gemini 3.5 Flash 作为正式扫描主模型。

建议先按以下路由做同源聊天记录 A/B 验证：

```text
daily_topic_scan
→ DeepSeek-V4-Flash-0731

daily_event_refine
→ GPT-5.6 Luna
→ Gemini 3.1 Pro（仅失败切换）
```

Gemini 3.5 Flash 保留为对照组，不进入正式任务池；Grok 4.5 暂不加入，避免同一日报的风格和判断标准随模型切换。

这个结论是“待真实群聊 A/B 验证的首选路由”，不是仅凭公开榜单宣布最终胜负。插件扫描分片约为 120 条或 8000 字符，远小于 128K/1M 长上下文评测，公开长上下文成绩只能作为间接证据。

## 官方证据

### DeepSeek-V4-Flash-0731

- DeepSeek 2026-07-31 更新说明明确写明：0731 保持 Preview 的架构和规模，只进行了再后训练。
- 0731 公布的新增成绩集中在 Agent、工具调用和代码任务，没有公布新的 MRCR、CorpusQA、LongBench 或群聊总结成绩。
- 因此不能把 0731 的 Agent 涨分直接解释为长文本总结或事件召回同步提升。
- Preview 技术报告中，DeepSeek-V4-Flash-Base 的 LongBench-V2 为 44.7，高于 DeepSeek-V3.2-Base 的 40.2。
- 同一技术报告中，DeepSeek-V4-Flash-Max 的 1M MRCR 为 78.7，CorpusQA 为 60.5；这些成绩依赖 Max 推理设置，不能直接代表当前网关默认调用方式。

来源：

- https://api-docs.deepseek.com/updates/
- https://arxiv.org/html/2606.19348

### Gemini 3.5 Flash 与 Gemini 3.1 Pro

Google 同一模型卡表格中的 GDM-MRCR v2（8-needle）结果：

| 模型 | 128K 平均 | 1M 点值 |
|---|---:|---:|
| Gemini 3.5 Flash | 77.3% | 26.6% |
| Gemini 3.1 Pro | 84.9% | 26.3% |

这说明在 128K 多目标检索上，Gemini 3.5 Flash 明显落后于 Gemini 3.1 Pro；在 1M 点值上两者接近。Gemini 3.5 Flash 在部分 Agent、代码和知识工作指标上更强，但这些指标不能替代群聊事件召回。

来源：

- https://deepmind.google/models/model-cards/gemini-3-5-flash/
- https://deepmind.google/models/model-cards/gemini-3-1-pro/

### GPT-5.6 Luna 与 Grok 4.5

Google 的同表结果显示：

| 模型 | GDPVal-AA v2 | 128K MRCR v2 |
|---|---:|---:|
| GPT-5.6 Luna | 1584 | 74.8% |
| Grok 4.5 | 1535 | 81.4% |
| Gemini 3.1 Pro | 965 | 84.9% |

据此只能做任务适配推断：Luna 更适合作为短上下文的知识工作和成稿精炼候选；Gemini 3.1 Pro 更适合作为需要更强上下文保持的失败切换；Grok 4.5 没有显示出足以抵消风格不一致风险的独占优势。

来源：

- https://deepmind.google/models/gemini/flash/

## A/B 验收方法

固定同一天、同一个群、同一份原始消息、相同提示词和相同分片，至少运行三组：

1. DSV4F-0731 扫描 + Luna 精炼。
2. Gemini 3.1 Pro 扫描 + Luna 精炼。
3. Gemini 3.5 Flash 扫描 + Luna 精炼。

人工评分不看文风偏好，优先检查：

- 主要事件召回率和普通话题覆盖率；
- 起止时间、发言人、链接和原话是否能落回源消息；
- 是否把不同话题错误合并；
- 是否把同一话题重复拆分；
- JSON 解析失败、超时、重试和拆分次数；
- 主要事件精炼成功数；
- 总耗时仅作为次要指标。

只有 DSV4F-0731 在真实数据上不低于其他组的事实准确度和覆盖率，才将其固定为正式扫描模型。

## 已恢复的工程状态

- 本地分支：`codex/v3.4-model-routing`
- 当前提交：`f8a82e5 feat: route scan and refinement to dedicated tasks`
- 工作区：干净
- 回归测试：29/29 通过
- 服务器此前部署：日报插件 v3.4.0；独立静默守卫只保护群 `188637136`
- MaiBot 主体已有两个插件专用任务：`daily_topic_scan`、`daily_event_refine`
- 现有 `replyer`、`planner`、`utils` 不应随日报模型实验修改

## 服务器待执行项

1. 只读读取当前 `model_config.toml` 中可用模型的精确名称，不输出 API Key。
2. 备份模型配置和当前插件配置。
3. 先只修改两个日报专用任务，不修改 `replyer`、`planner`、`utils`。
4. 运行三组同源 A/B，并保存覆盖率、精炼成功率、超时与解析失败数据。
5. 根据真实数据固定正式路由；效果不佳时恢复 v3.4.0 当前模型配置。
6. 验证独立静默守卫、MaiBot Core、NapCat 和私聊投递均正常。
