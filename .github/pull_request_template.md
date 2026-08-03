## 变更说明

<!-- 说明改了什么，以及为什么需要这项变更。 -->

## 影响范围

- [ ] 总结提取、编排或事实复核
- [ ] 个人事实画像
- [ ] 普通版安装包
- [ ] 多模型增强版与 MaiBot 高级任务路由
- [ ] 文档、测试或发布工具

## 验证清单

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m unittest discover -s extras/tests -v`
- [ ] `python scripts/build_release.py`
- [ ] `dist/SHA256SUMS.txt` 校验通过
- [ ] 普通版与增强版的共同总结核心保持一致
- [ ] 普通版不包含 `extras/`，增强版包含任务路由说明与工具
- [ ] 未提交真实 `config.toml`、密码、Token、API Key 或私钥

## 发布说明

<!-- 标明这是开发验证、候选版还是正式发布；默认 PR 不创建 Release 或标签。 -->
