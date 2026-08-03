# 发布架构

本仓库只维护一套插件源码。普通版与多模型增强版在构建阶段生成，不使用长期分叉分支。

## 唯一共同核心

以下路径同时进入两个安装包，并在构建时逐文件校验 SHA256：

- `plugin.py`、`_manifest.json`
- `core/`、`templates/`、`fonts/`、`tests/`
- `CHANGELOG.md`、`LICENSE`、`SECURITY.md`

## 安装包差异

- `standard`：使用 `packaging/standard/` 中的 README 和示例配置，不包含 `extras/`。
- `multimodel`：使用 `packaging/multimodel/` 中的 README 和示例配置，并包含 `docs/MODEL_ASSIGNMENT.md` 与 `extras/`。
- 任一专用模型任务未配置或未安装宿主补丁时，插件回退到 MaiBot 的 `replyer`。

## 构建

在仓库根目录执行：

```bash
python scripts/build_release.py
```

输出位于未纳入 Git 的 `dist/`：两个安装目录、两个 ZIP、共同核心比对、文件清单和 SHA256 清单。
