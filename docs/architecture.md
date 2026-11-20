# 项目结构

```text
src/web/          浏览器源码、主题、构建后资源、地图与图标
src/gateway/      登录页、关于页
scripts/          Python 服务、业务模块、构建和运维工具
data/             基础菜谱、公开营养数据
tests/            隔离测试与浏览器辅助服务
docs/             面向部署者和贡献者的文档
```

浏览器经 HTTPS 反向代理访问 `public_gateway.py`，网关处理登录、账号、同源检查和资源边界，再签名转发到回环地址上的 `work_server.py`。后端使用 `tenant_storage.py` 的请求上下文选择账号目录；共享厨房另行验证成员关系。

业务模块直接使用 SQLite 与受控附件目录。财务金额以整数分存储；查询与统计不调用模型。山君只参与明确请求的分析。

构建流程由 `build_release.py` 组合 CSS/JavaScript，使用 Terser 压缩 JavaScript，生成带内容哈希的 10 个资源和 `release-assets.json`。服务启动校验 HTML、哈希与清单一致。修改浏览器源码后必须重新构建。

此仓库以 Web 应用与 Python 服务作为独立的开源起点，不包含部署者的运行数据、模型凭据、业务附件与私人运维配置。测试集合与验证结果以当前检出版本为准。
