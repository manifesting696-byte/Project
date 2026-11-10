# 企业数据底座智能问析 Agent：插件化架构审计与重构设计

## 一、任务背景

当前项目已经完成第一版代码开发，系统采用 Python、LangGraph、LLM、RAG、PostgreSQL 等技术。

现在需要对现有代码进行一次架构级审查。

本次审查的目标不是简单地拆分文件、增加抽象类或重命名模块，而是参考 DeepSeek Harness 的插件化架构思想，重新审视系统是否具备良好的模块解耦、能力替换、插件注册、动态组合和独立扩展能力。

核心设计原则：

**Core Runtime 负责运行机制，Plugin 负责能力实现，Workflow 负责业务编排，Profile 负责能力组合。**

参考 DeepSeek Harness 的 Capability Seam 思想，一个可替换能力应具备：

1. Service Definition：统一接口定义。
2. Service Provider：具体能力实现。
3. Consumer：使用该能力的业务组件。

三者之间通过接口和依赖注入连接，避免直接依赖具体实现。

## 二、第一阶段：现有架构扫描

请先完整阅读项目代码，分析：

1. 当前目录结构和模块划分。
2. 核心模块之间的依赖关系。
3. Agent、Workflow、Tool、LLM、Database、Memory 的职责边界。
4. 是否存在循环依赖和跨层依赖。
5. 是否存在大量直接 import 具体实现的情况。
6. 是否存在全局变量、单例和隐式共享状态。
7. 是否存在 Agent 直接依赖具体模型 SDK 的情况。
8. 是否存在业务逻辑与 LangGraph 节点实现强耦合的情况。

请绘制当前系统的模块依赖图，并指出主要耦合问题。

## 三、第二阶段：Capability Seam 审查

逐一审查以下能力是否实现了接口与实现分离：

- LLM Provider
- Agent Provider
- Database Provider
- Semantic Catalog
- Metadata Provider
- Profiling Provider
- Retrieval Provider
- Memory Provider
- Tool Provider
- Evaluation Provider
- Observability Provider
- Report Provider

对每项能力检查：

1. 是否有独立接口定义？
2. 是否有具体 Provider 实现？
3. 是否有 Consumer 使用接口而非具体实现？
4. 能否在不修改 Consumer 的情况下替换 Provider？
5. 是否支持配置化选择实现？
6. 是否存在资源初始化、关闭和错误处理机制？

不要为了插件化而强制将每个小函数设计成插件。应根据能力边界、替换价值和生命周期判断插件粒度。

## 四、第三阶段：Core Runtime 审查

分析是否需要建立独立的最小运行时。

重点审查：

- Plugin Registry：插件注册与发现。
- Plugin Loader：插件加载。
- Dependency Injection：依赖注入。
- Event Bus：事件分发。
- Lifecycle Manager：插件生命周期。
- Agent Runtime：Agent 执行机制。
- State Manager：任务状态管理。

明确哪些能力属于 Core，哪些应该属于 Plugin。

Core 不应直接包含具体业务指标、数据库表结构、模型 SDK 或供应链分析规则。

同时评估是否可以基于现有 Python 生态实现，而不是盲目复制 DeepSeek Harness 的 Cordis。

## 五、第四阶段：插件机制设计

设计统一插件协议，至少包含：

- plugin_id
- version
- dependencies
- provides
- config_schema
- initialize
- shutdown

设计插件注册、依赖检查、加载、卸载、冲突检测和配置覆盖机制。

明确插件如何获取其他服务，如何注册事件监听，以及卸载时如何清理资源。

设计 Profile 组合机制，例如：

- lightweight：轻量单 Agent。
- default：标准数据问析。
- full：完整动态规划与长链路分析。

要求插件组合只改变配置，不需要修改业务代码。

## 六、第五阶段：针对本项目重新设计插件体系

重点考虑以下插件：

### 1. LLM

DeepSeek、OpenAI、Qwen 等模型实现可以替换。

### 2. Semantic

Schema 扫描、数据画像、关系发现、指标管理、语义目录、语义检索可以按能力拆分。

### 3. Database

支持 PostgreSQL、MySQL 等数据库 Provider。

### 4. Retrieval

支持 BM25、向量检索和混合检索。

### 5. Agent

Supervisor、Semantic Agent、Query Agent、Analysis Agent、Presentation Agent。

### 6. Tools

SQL、Python、图表、报告等工具。

### 7. Memory

Checkpoint、Store、上下文压缩、结果卸载和回读。

### 8. Observability

Langfuse、OpenTelemetry 等监控实现。

### 9. Evaluation

SQL 正确性、语义绑定、任务规划、长链路成功率等评测。

分析这些模块是否应该独立成插件，给出合理的粒度，不要机械地将每个模块都拆成独立插件。

## 七、第六阶段：依赖关系和事件机制

设计模块依赖图，明确：

- 哪些插件依赖哪些服务。
- 哪些插件可以独立加载。
- 哪些插件必须先于其他插件初始化。
- 哪些事件可以扩展 SQL 执行流程。
- 如何在不修改 Agent Loop 的情况下增加安全检查、数据质量检查和评测。

建议定义标准事件：

- semantic/before_bind
- semantic/after_bind
- sql/before_execute
- sql/after_execute
- agent/before_step
- agent/after_step
- analysis/after
- report/before_generate

明确事件参数、执行顺序、异常传播和取消机制。

## 八、第七阶段：可插拔性测试

不要仅通过代码结构判断插件化是否完成。

必须设计实际测试：

1. 将 DeepSeek Provider 替换成 OpenAI Provider，Query Agent 不修改。
2. 将 YAML Semantic Catalog 替换成 PostgreSQL Semantic Catalog，Semantic Agent 不修改。
3. 将 PostgreSQL Provider 替换成 Mock Provider，核心查询流程不修改。
4. 新增一个 Python Analysis Tool，不修改 Agent Runtime。
5. 禁用 Presentation Agent，数据查询仍然可以运行。
6. 禁用 Langfuse，核心问析流程仍然可以运行。
7. 加载存在依赖缺失的插件时，系统能够给出明确错误。
8. 卸载插件后，其注册的服务和事件监听能够正确清理。

## 九、第八阶段：输出架构审计报告

请按以下格式输出：

1. 当前代码架构分析。
2. 当前模块依赖图。
3. 主要耦合问题及严重程度。
4. 插件化成熟度评估。
5. 推荐的目标架构图。
6. Capability Seam 接口设计。
7. Core Runtime 设计。
8. Plugin Registry 与生命周期设计。
9. Profile 与 Bundle 组合设计。
10. 推荐目录结构。
11. 当前代码与目标架构的差异。
12. 分阶段重构计划。
13. 可插拔性测试方案。

问题按照 P0、P1、P2、P3 分类。

## 十、重要约束

1. 第一轮只做审查和架构设计，不修改源代码。
2. 不要为了追求插件数量而过度拆分。
3. 不要将所有 LangGraph 节点都强制设计成插件。
4. 不要把插件注册中心设计成一个新的庞大单体。
5. 保留已经合理的业务逻辑和数据结构。
6. 优先通过接口抽象、依赖注入和配置组合实现解耦。
7. 对每个架构问题必须提供实际代码证据。
8. 明确哪些是必须重构的，哪些只是未来扩展建议。

最终目标是让系统具备良好的模块独立性、能力可替换性和扩展性，而不是单纯模仿 DeepSeek Harness 的目录结构。