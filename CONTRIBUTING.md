# 参与贡献

感谢你帮助改进 MaiBot 私聊群事件日报。这个仓库以“事实性优先、结果可回查、安装边界清晰”为维护原则。

## 仓库结构

- `plugin.py`：插件入口、配置、命令、定时任务和 MaiBot SDK 对接。
- `core/`：提取、事件合并、事实复核和渲染等共同总结核心。
- `templates/`、`fonts/`：日报与个人画像的可视化资源。
- `tests/`：共同核心与插件任务路由测试。
- `extras/`：可选的 MaiBot 四任务高级模型路由补丁、安装器和测试。
- `packaging/standard/`：普通版专用说明与示例配置。
- `packaging/multimodel/`：多模型增强版专用说明与示例配置。
- `scripts/build_release.py`：从同一源码构建并校验两个安装包。

普通版和多模型增强版必须共享完全相同的总结核心。两者只允许在安装说明、示例配置以及是否携带 `extras/` 方面存在差异。

## 本地验证

建议使用 Python 3.11 或 3.13，并安装 Jinja2：

```bash
python -m pip install --upgrade jinja2
python -m unittest discover -s tests -v
python -m unittest discover -s extras/tests -v
python -m py_compile plugin.py core/analysis.py core/constants.py core/event_digest.py core/rendering.py
python scripts/build_release.py
```

在 Linux 或 GitHub Actions 中可继续校验构建清单：

```bash
cd dist
sha256sum -c SHA256SUMS.txt
```

完整功能仍需在 MaiBot 宿主中验证插件加载、配置生命周期、消息读取、模型调用、图片渲染和管理员私聊发送。

## 变更约束

- 总结内容应能追溯到源消息，不得把推测包装成事实。
- 不新增活跃度、MBTI、群友排名、金句排行或情绪指数等娱乐分析。
- 个人画像只保留有证据支持的事实型内容。
- 插件不得保存供应商 API Key、模型 URL、服务器密码或平台 Token。
- 四个插件专用任务为 `plugin_daily_extract`、`plugin_daily_compose`、`plugin_daily_verify`、`plugin_user_profile`；未配置时必须安全回退到 MaiBot 的 `replyer`。
- 普通版不得要求用户修改 MaiBot；增强版的宿主补丁必须保持可选、可检查、可重复执行且可回滚。

## 提交 Pull Request

1. 从最新 `main` 创建 `codex/` 或功能分支。
2. 只提交本次变更涉及的文件，不提交 `dist/`、缓存和真实配置。
3. 完成本地测试与双包构建。
4. 在 PR 中说明变更目的、用户影响、验证结果和发布状态。
5. 默认先提交 Draft PR；除非维护者明确决定，否则不要自动合并、创建标签或正式 Release。

涉及发布包的变更还应确认：

- `CORE_COMPARISON.txt` 的结果为 `IDENTICAL`。
- 普通版不含 `extras/`。
- 增强版包含 `MODEL_ASSIGNMENT.md`、`extras/README.md`、补丁和安装器。
- `SHA256SUMS.txt` 中所有文件均可通过校验。
