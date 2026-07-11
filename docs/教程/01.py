import asyncio
from datetime import datetime, timedelta, timezone

from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# 中国时区，不依赖 Windows zoneinfo/tzdata
CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


client = AsyncOpenAI(
    api_key="sk-f46daf0bcea54a15bd5e3fac9cbe7447",
    base_url="https://api.deepseek.com/v1",
)

model = OpenAIChatModel(
    "deepseek-v4-flash",
    provider=OpenAIProvider(openai_client=client),
)
agent = Agent(
    model=model,
    deps_type=str,
    instructions="全程使用中文回答。用户问当前时间时，必须调用 get_time 工具。",
)

# @agent.tool_plain不需要参数
# 区别只在第一个参数
# Copy code to clipboard
# @agent.tool_plain         函数签名只能是:  (工具参数...) -> 返回值
# @agent.tool                函数签名必须是:  (ctx: RunContext, 工具参数...) -> 返回值

#deps从哪里来

# 生产中的真实来源
# 教程里是写死的 "张三"，实际项目里用户名来自 JWT 认证的用户信息：
# Copy code to clipboard
# # 真实 FastAPI 路由里
# @app.post("/chat/stream")
# async def chat_stream(request: Request, current_user: User = Depends(is_authed)):
#     ...
#     async for result in agent.run_stream_events(
#         user_message,
#         deps=current_user.username,   # ← 从 JWT 解析出来的真实用户名
#     ):
#         ...
# 这就是 @agent.tool + RunContext 的设计价值——把「运行时才有的东西」（当前用户、数据库连接、请求上下文）注入到工具里，而不是靠全局变量或环境变量。tool_plain 做不到这一点，它只能访问函数自己的参数。

# @agent.tool
# def current_user_name(ctx: RunContext[str]) -> str:
#     """获取当前聊天用户的名称，仅在用户要求个性化问候时调用

#     典型场景：客户端调用 API 时通过 deps 注入当前用户名，
#     工具函数通过 ctx.deps 拿到它，不需要从全局变量或环境变量中获取。
#     """
#     return f"当前用户是 {ctx.deps}，对他说你好"


@agent.tool_plain
def get_time() -> str:
    """获取当前时间"""
    return datetime.now().isoformat(timespec="seconds")

# 普通流式聊天：用 run_stream()
# 需要观察工具调用过程：用 run_stream_events()
# 需要逐节点控制 Agent 图执行：用 iter()


async def main():
    async with agent.run_stream_events("现在几点", deps="张三") as events:
        async for event in events:
            if event.event_kind == "part_delta":
                delta = event.delta
                kind = getattr(delta, "part_delta_kind", None)

                if kind in ("text", "thinking"):
                    content = getattr(delta, "content_delta", None)
                    if content:
                        print(content, end="", flush=True)

                elif kind == "tool_call":
                    tool_name = getattr(delta, "tool_name_delta", None)
                    if tool_name:
                        print(f"\n【开始调用工具】{tool_name}")

                    args_delta = getattr(delta, "args_delta", None)
                    if args_delta:
                        pass

            elif event.event_kind == "function_tool_call":
                print(f"\n【工具调用】{event.part.tool_name}")
                print(f"参数: {event.part.args}")

            elif event.event_kind == "function_tool_result":
                print(f"\n【工具返回结果】{event.content}")

            elif event.event_kind == "agent_run_result":
                usage = event.result.usage
                print("\n\n==== Token汇总 ====")
                print(f"输入token：{usage.input_tokens}")
                print(f"输出token：{usage.output_tokens}")
                print(f"总token：{usage.total_tokens}")

                print(f"新消息: {event.result.new_messages()}")


asyncio.run(main())
