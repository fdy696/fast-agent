"""SAQ Worker 启动脚本."""
import asyncio
import sys
sys.path.insert(0, "src")

from saq import Worker
from utils.queue import queue
from agent.worker_tasks import FUNCTIONS


async def main():
    names = [f.__name__ for f in FUNCTIONS]
    print(f"SAQ Worker starting, functions: {names}")
    worker = Worker(queue, functions=FUNCTIONS, concurrency=10)
    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
