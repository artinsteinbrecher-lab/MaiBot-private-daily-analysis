# 发布流程

本仓库发布两个安装包。它们共享完全相同的总结核心，只在安装说明、示例配置和是否携带 `extras/` 方面不同。

## 发布前条件

- `_manifest.json` 使用有效的语义化版本号，`CHANGELOG.md` 已包含同版本条目。
- 候选版本已在真实 MaiBot 宿主完成插件加载、群消息读取、四阶段模型调用、渲染和管理员私聊发送验证。
- `standard` 安装不要求修改 MaiBot；`multimodel` 的宿主补丁已经执行检查、应用、重复应用和回滚测试。
- 仓库与安装包不包含真实 `config.toml`、账号、聊天内容、Token、API Key、密码或私钥。

## 本地验证

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m unittest discover -s extras/tests -v
python -m py_compile plugin.py core/analysis.py core/constants.py core/event_digest.py core/rendering.py
python scripts/build_release.py
cd dist
sha256sum -c SHA256SUMS.txt
```

确认 `CORE_COMPARISON.txt` 包含 `result=IDENTICAL`，普通版不含 `extras/`，增强版包含 `MODEL_ASSIGNMENT.md` 和完整 `extras/`。
两个包都应包含 `scripts/build_release.py`，使随包提供的发布构建回归测试可以在解压目录独立运行。

## GitHub 发布

1. 通过 Pull Request 将候选分支合并到受保护的 `main`。
2. 等待 `main` 的 `Validate` 工作流全部通过。
3. 在 `main` 当前提交创建带注释的 `vX.Y.Z` 标签并推送。
4. 创建同名 GitHub Release，正文概括事实性、兼容性、安装差异和已知限制。
5. 上传以下附件：

   - `khiqwq_daily_analysis-vX.Y.Z-standard.zip`
   - `khiqwq_daily_analysis-vX.Y.Z-multimodel.zip`
   - `SHA256SUMS.txt`
   - `CORE_COMPARISON.txt`
   - `FILE_MANIFEST.txt`

GitHub 自动生成的 Source code 压缩包不是安装包。不得用它们替代上述两个附件。

## 发布后验证

- 从 Release 页面重新下载附件并核对 SHA256。
- 用全新目录解压普通版，确认没有 `extras/` 和真实配置。
- 解压增强版，运行安装器 `--check`，确认未知 MaiBot 版本会安全拒绝。
- 确认 Release 标记为 Latest，README 下载说明与版本一致。
- 保留已有正式标签和 Release；只删除已经合并且不再需要的临时开发分支。
