# 已发布语义目录

此目录保存已审核的版本文件 `<version>.yaml` 及配套 `<version>.sha256`。运行时通过 `SEMANTIC_CATALOG_VERSION` 选择版本，并校验文件哈希、发布状态和快照身份。

默认语义源位于 `config/semantic/catalog.yaml`。业务负责人审核后才能标记为 published，候选指标的定义与状态保存在语义源中。
