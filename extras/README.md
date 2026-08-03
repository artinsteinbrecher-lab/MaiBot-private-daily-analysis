# MaiBot 高级模型任务兼容补丁

本目录只属于“多模型增强改包版”。插件核心与普通版完全相同；不修改 MaiBot 时，
四个 LLM 流程仍统一使用 `replyer`，总结完整性、事实约束和版式不会缩水。

兼容补丁只向 MaiBot 注册四个高级任务：

- `plugin_daily_extract`：事实、事件与证据提取
- `plugin_daily_compose`：主要事件编排、压缩与去重
- `plugin_daily_verify`：独立事实复核，只允许删除声明或降低状态
- `plugin_user_profile`：事实型个人画像

补丁不写入供应商、URL、API Key 或模型 ID。任一专用任务留空时，由 MaiBot 宿主真实回退到 `replyer`。

## 支持边界

安全安装脚本当前只支持：

- MaiBot 源码树同时存在四个目标文件；
- `MODEL_CONFIG_VERSION = "1.17.6"` 的干净基线；
- 或已经由本脚本完整升级到 `1.17.7` 的幂等状态。

脚本不依赖无关的 `CONFIG_VERSION`，因此不会再被 `8.14.33` 与 `8.14.34` 这类宿主配置版本差异误伤。
遇到未知模型配置版本、只改了一部分、目标锚点不唯一或文件非 UTF-8 时，脚本会拒绝写入。
不要把“检测通过”理解为兼容所有 MaiBot 版本。

## 推荐流程

以下命令均在插件 `extras/` 所在位置执行，`/path/to/MaiBot` 替换为 MaiBot 源码根目录。

### 1. 只读检测

```bash
python3 install_maibot_task_routing.py /path/to/MaiBot --check
```

`pristine` 表示可应用；`installed` 表示已经完整安装。`unknown` 或 `partial` 必须先人工处理，不能强制覆盖。

### 2. 备份并应用

```bash
python3 install_maibot_task_routing.py /path/to/MaiBot --apply
```

脚本在写入前备份且只修改：

```text
src/config/model_configs.py
src/config/default_model_config.py
src/llm_models/utils_model.py
src/config/config.py
```

备份位于 MaiBot 根目录下的 `.khiqwq_daily_analysis_backups/<UTC时间>/`，命令会输出精确的 `BACKUP_DIR`。
再次执行 `--apply` 时，如果四项已完整安装，不会重复插入或新建备份。

### 3. 重建与配置

按原部署方式重新构建或重启 MaiBot，然后只在 MaiBot“高级模型任务”中填写需要独立分配的任务。
某项留空即跟随 `replyer`。插件设置中无需、也不应填写供应商、URL、Key 或模型 ID。

### 4. 回滚

```bash
python3 install_maibot_task_routing.py /path/to/MaiBot --rollback /path/to/BACKUP_DIR
```

回滚前会校验当前四个文件仍与本脚本应用后的哈希一致；如果安装后又被其他升级或人工编辑改变，
脚本会拒绝覆盖，避免丢失新改动。成功回滚后，四个任务恢复为普通版统一跟随 `replyer`。

## 人工参考补丁

`maibot-plugin-task-routing.patch` 只用于代码审阅或手工移植。它使用零上下文差异，避免绑定无关
`CONFIG_VERSION`，因此手工检查与应用必须显式允许零上下文：

```bash
git apply --check --unidiff-zero maibot-plugin-task-routing.patch
git apply --unidiff-zero maibot-plugin-task-routing.patch
```

人工补丁仍不应代替安装脚本的版本、部分安装、备份与回滚校验。

## 本地测试

```bash
python3 -m unittest discover -s extras/tests -v
```
