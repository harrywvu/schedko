import asyncio

BACKGROUND_TASKS: set[asyncio.Task] = set()
PROCESSING_TASKS: dict[str, asyncio.Task] = {}
