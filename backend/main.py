"""
情绪疏导 Agent — 多轮对话 CLI 入口（新架构）。

三条线并行：对话线（流式说话）/ 决策线（安全 + 疗法）/ 沉淀线（画像 + 摘要）。
"""

import asyncio
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from src.bootstrap import build_scheduler


async def main():
    print("=" * 60)
    print("Emotion Support Agent（三线并行新架构）")
    print("=" * 60)
    print()
    print("本系统不能替代专业心理咨询或治疗。")
    print("输入 /quit 退出。")
    print()

    scheduler = build_scheduler()
    session_id = scheduler.sessions.create("cli-user")

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit"):
            print("Take care. Goodbye.")
            break

        print("\nAgent: ", end="", flush=True)
        final_data = {}

        async for event in scheduler.handle_message("cli-user", session_id, user_input):
            if event["event"] == "token":
                print(event["data"], end="", flush=True)
            elif event["event"] == "final":
                final_data = event["data"]
            elif event["event"] == "error":
                print("[生成失败，请重试]", end="", flush=True)
        print()
        if final_data.get("crisis"):
            print("[安全提醒：如你处于危机中，请联系信任的人或心理援助热线。]")


if __name__ == "__main__":
    asyncio.run(main())
