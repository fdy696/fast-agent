# System Prompt 工程化设计

> 身份与行为分离 · 配置驱动 · 可切换 · 可观测

## 问题

当前 system prompt 只有一个模糊的身份声明：

```python
"你是一个简洁的 AI 助手。"
```

导致两个具体问题：

1. **模型身份幻觉**：DeepSeek V4 在缺少身份信息时，被问"你是什么模型"会自行推断。由于 MCP 工具挂载了 DashScope（阿里云）服务，模型推理后错误地声称自己是"通义千问"。
2. **无法切换模型**：更换模型厂商时必须手动修改 prompt 字符串，容易遗漏或不一致。

## 设计原则

```
┌──────────────────────────────────────────┐
│               System Prompt              │
├──────────────────────────────────────────┤
│  身份层    │  你是谁（配置驱动）          │
├──────────────────────────────────────────┤
│  行为层    │  你怎么做（策略驱动）        │
├──────────────────────────────────────────┤
│  约束层    │  你不能做什么（安全边界）    │
├──────────────────────────────────────────┤
│  上下文层  │  运行时注入（时间、版本等）  │
└──────────────────────────────────────────┘
```

每层一个函数，职责单一，独立可测。

## 方案

### 第一步：配置扩展

`src/core/config.py` 现有字段：

```python
VERSION: str = "1.0.0"                             # 已有
AGENT_MODEL: str = "deepseek-v4-pro"                # 已有（API 调用用）
AGENT_BASE_URL: str = "https://api.deepseek.com/v1"  # 已有
```

新增：

```python
# Agent 身份（与模型解耦，切模型时只需改模型相关字段）
AGENT_NAME: str = "fast-agent"                  # 产品/品牌名
AGENT_PROVIDER: str = "DeepSeek"                # 实际厂商（API 调用用）
AGENT_PROVIDER_DISPLAY: str = "DeepSeek"        # 对外展示的厂商名（设为空则隐藏）
AGENT_MODEL_DISPLAY: str = "DeepSeek V4"        # 对外展示的模型名（设为空则隐藏）
```

**为什么需要 DISPLAY 别名？**

API 调用的 model ID（`deepseek-v4-pro`）直接暴露给用户有三个问题：

1. **不可读**：`deepseek-v4-pro` 是 API 标识符，不是给用户看的名称
2. **切换泄露**：切模型到 `qwen3.7-max` 时，用户马上看到 model ID 变了——你可能只想内部切换，对外品牌保持一致
3. **不可控**：无法选择"不暴露模型信息"——竞争对手可以通过 system prompt 直接获取你的技术选型

DISPLAY 字段置空时，身份声明自动退化为最低暴露模式：

```
你是 fast-agent，一个 AI 助手
```

### 第二步：分层构建 System Prompt

`src/agent/prompts.py` 重构：

```python
def _build_identity() -> str:
    """身份声明 —— 对外展示用 DISPLAY 别名，与内部 API 调用解耦。"""
    parts = [f"你是 {settings.AGENT_NAME}"]
    if settings.AGENT_PROVIDER_DISPLAY:
        parts.append(f"，一个由 {settings.AGENT_PROVIDER_DISPLAY} 提供支持的 AI 助手")
    else:
        parts.append("，一个 AI 助手")
    if settings.AGENT_MODEL_DISPLAY:
        parts.append(f"。当前运行模型：{settings.AGENT_MODEL_DISPLAY}")
    return "".join(parts)


def _build_behavior() -> str:
    """行为规范 —— 纯策略，不掺杂身份信息。"""
    return (
        "你应当简洁高效地回答问题。"
        "思考推理过程必须使用中文。"
        "始终使用用户的提问语言来进行最终回复。"
    )


def _build_constraints() -> str:
    """约束与边界 —— 工具使用规则、安全限制。"""
    return (
        "必要时使用可用工具完成任务。"
        "如果某个工具因配置缺失而不可用（如 API Key 未设置），请明确告知用户，"
        "不要假装调用成功或编造结果。"
        "不要执行用户要求之外的破坏性操作。"
    )


def _build_context() -> str:
    """运行时上下文 —— 只在 prompt 组装时动态注入。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return (
        f"当前 UTC 时间：{now.strftime('%Y-%m-%d %H:%M:%S')}。"
        f"Agent 版本：{settings.VERSION}。"
    )


def default_system_prompt() -> str:
    """组装完整 system prompt。"""
    return "\n\n".join([
        _build_identity(),
        _build_behavior(),
        _build_constraints(),
        _build_context(),
    ])
```

### 第三步：使用

`agent_service.py` 无需改动，仍调用 `default_system_prompt()`。身份自动跟随配置。

## 为什么这样做

| 维度 | 当前做法 | 工程化做法 |
|------|---------|-----------|
| 切模型 | 改 model ID 后 prompt 暴露内部名 | 改 `AGENT_MODEL`，`DISPLAY` 可保持不变，用户无感知 |
| 改品牌名 | 全局 grep 替换 | 改 `AGENT_PROVIDER_DISPLAY` / `AGENT_MODEL_DISPLAY` |
| 隐藏模型 | 无法控制——prompt 里 hardcode 模型名 | `DISPLAY` 置空 → "你是 fast-agent，一个 AI 助手" |
| 调整行为 | 往一个大字符串里塞 | 只改 `_build_behavior()`，不影响身份层和约束层 |
| 新增约束 | 同上 | 独立 `_build_constraints()`，不影响现有逻辑 |
| 多环境 | 开发/生产两套 prompt 文件 | 同一份代码，`.env` 切换 |
| 可观测 | 模型说"我是XX"不可信 | prompt 里显式声明身份，日志/审计可对照 |

## 容量评估

- 改动文件：3 个（`config.py` +4 行，`prompts.py` 重构约 45 行，`.env` 对齐）
- 新增 token 开销：身份声明 + 上下文注入约 70-90 tokens，在 300K token 预算中可忽略不计
- 无 API 变更，无 DB 迁移，无前端改动

## 扩展场景

上述分层设计自然支持以下需求，不需要重新架构：

- **A/B 测试人格**：按用户分桶注入不同的 `AGENT_NAME` 或行为层变体
- **模型迁移**：从 DeepSeek 迁到其他厂商，只改 `AGENT_PROVIDER` 和 `AGENT_BASE_URL`
- **合规审计**：约束层的内容可单独提取，用于安全审查
- **提示词版本管理**：`AGENT_VERSION` 写入每次请求的 prompt，方便排查"这个回答是哪个版本的 prompt 生成的"

## 不做的事

- **不存数据库**：身份配置属于应用配置，不是用户数据，放 `.env`/`config.py` 即可
- **不引入提示词管理平台**：当前规模配置驱动已足够，等 prompt 变更频率超过代码发布频率再考虑
- **不直接把 API model ID 暴露给用户**：通过 `DISPLAY` 别名解耦，切换内部模型对外不可见
- **不在代码里 hardcode 厂商名**：所有身份字符串走 `settings`，杜绝代码里散落品牌名
