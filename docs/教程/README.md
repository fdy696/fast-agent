# Pydantic AI 工程学习教程

本教程分三篇，建议按顺序阅读：

1. **[入门篇：第一个 Agent](入门篇-第一个Agent.md)** — 创建能跑起来的 Agent
2. **[工具篇：工具调用与状态管理](工具篇-工具调用与状态管理.md)** — 让 Agent 调用外部能力
3. **[工程篇：生产级 Agent 系统](工程篇-生产级Agent系统.md)** — 搭建可扩展的 Agent 架构

## 学习路径

```
入门篇（30分钟） → 工具篇（60分钟） → 工程篇（90分钟）
```

### 入门篇
- 适合零基础学习者
- 学会创建非流式和流式 Agent
- 理解 model、instructions、user prompt 的关系
- 掌握常见坑的避免方法

### 工具篇
- 需要已完成入门篇
- 学会 @agent.tool 和 @agent.tool_plain
- 掌握多轮对话消息历史管理
- 理解 ModelRetry 和 UsageLimits

### 工程篇
- 需要已完成工具篇
- 掌握 FunctionToolset、MCPToolset
- 理解 Agent Skills 渐进式加载
- 学会用 Capability 统一横切关注点

## 旧版文档

原《Pydantic AI 从入门到精通》单文件版本已移至 `../pydantic-ai-comprehensive-guide-v1-已废弃.md`。
