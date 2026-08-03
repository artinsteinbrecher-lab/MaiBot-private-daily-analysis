# 文档索引

按你的身份选择入口，不需要从头阅读全部文件。

## 普通安装者

1. 从[仓库首页](../README.md#下载选择)下载 standard ZIP。
2. 按首页的[五分钟快速开始](../README.md#五分钟快速开始普通版)安装并完成一次管理员私聊验证。
3. 需要全部配置字段、命令和故障排查时，阅读[普通版完整说明](../packaging/standard/README.md)。

普通版不修改 MaiBot，不注册新的全局模型任务，四个 LLM 流程统一跟随 `replyer`。

## 多模型增强版安装者

1. 先把 multimodel 当作普通插件安装并验证成功。
2. 阅读[增强版完整说明](../packaging/multimodel/README.md)了解增强版边界。
3. 按[宿主扩展操作说明](../extras/README.md)对实际 MaiBot 源码树执行只读检查、备份、应用、重建和复核。
4. 最后在 MaiBot 高级模型任务中配置四个插件专用任务；留空项继续跟随 `replyer`。

不要在插件设置中重复填写供应商、URL、API Key 或模型 ID。

## 模型选型与验收

- [MODEL_ASSIGNMENT.md](MODEL_ASSIGNMENT.md)：接入后的初始模型分配建议与真实性优先边界。
- [EVALUATION.md](EVALUATION.md)：当前总结核心的能力矩阵和方案取舍。
- [history/](history/)：带日期的历史评估记录，不代表永久有效的供应商配置。

## 维护者

- [ARCHITECTURE.md](ARCHITECTURE.md)：普通版与增强版如何共享同一核心。
- [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md)：候选版本审阅清单。
- [RELEASING.md](RELEASING.md)：测试、构建、标签、附件与发布后复核。
- [CONTRIBUTING.md](../CONTRIBUTING.md)：目录职责、变更约束和 Pull Request 要求。

安全问题和使用支持分别见 [SECURITY.md](../SECURITY.md) 与 [SUPPORT.md](../SUPPORT.md)。
