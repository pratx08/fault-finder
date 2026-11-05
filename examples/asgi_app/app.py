"""Minimal ASGI application; launch with uvicorn after installing it in your environment."""

from faultfinder.observer import FaultFinderASGI


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    elif scope["type"] == "http":
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"healthy"})


app = FaultFinderASGI(application)
