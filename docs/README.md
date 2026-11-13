# 项目文档

## 当前实现

- [字段、对象关系与数据流](architecture/字段与数据流.md)
- [插件运行时与数据表接入](architecture/插件运行时与数据表接入.md)
- [公开 Agent 架构对照](architecture/公开Agent架构对照.md)
- [PostgreSQL 部署与迁移](operations/PostgreSQL部署与迁移.md)
- [启动、验证与目录结构](../README.md)

## 黑盒测试资料

- [按六类组织的Query与预期结果](testing/Agent黑盒测试集.md)
- [可读取的案例JSON](../tests/blackbox/cases.json)
- [按五组评价字段组织的意图识别测试集](testing/意图识别测试集.md)
- [意图识别用例及固定上下文JSON](../tests/intent/cases.json)

## 参考资料

reference 保存原始需求文档和 [电子供应链数据表说明](reference/电子供应链数据表说明.md)。程序从该数据表说明读取字段含义。

## 历史记录

archive/v1、archive/v2 保存前两版设计，archive/acceptance 保存早期验收报告和模型轨迹。历史材料中的代码说明与命令对应当时的项目版本，当前入口见上方文档。
