import asyncio
import socket
from typing import Callable

import uvicorn
from fastapi import FastAPI


def serve(app: FastAPI, on_ready: Callable[[int], None], port: int = 0) -> None:
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    bound_port = sock.getsockname()[1]

    async def main():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done():
                await task
                return
            await asyncio.sleep(0.02)
        on_ready(bound_port)
        await task

    try:
        asyncio.run(main())
    finally:
        sock.close()
