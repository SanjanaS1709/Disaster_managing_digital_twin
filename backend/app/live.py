from fastapi import WebSocket
from typing import Any
import asyncio


class LiveClients:
    def __init__(self):
        self.clients: dict[WebSocket, asyncio.AbstractEventLoop] = {}

    async def connect(self, websocket: WebSocket, status: dict[str, Any]):
        await websocket.accept()
        self.clients[websocket] = asyncio.get_running_loop()
        await websocket.send_json({'type': 'SYNC', **status})

    def disconnect(self, websocket):
        self.clients.pop(websocket, None)

    async def broadcast(self, message):
        stale = []
        current_loop = asyncio.get_running_loop()
        for client, client_loop in tuple(self.clients.items()):
            try:
                if client_loop is current_loop:
                    await asyncio.wait_for(client.send_json(message), timeout=0.5)
                else:
                    future = asyncio.run_coroutine_threadsafe(client.send_json(message), client_loop)
                    await asyncio.wait_for(asyncio.wrap_future(future), timeout=0.5)
            except Exception:
                stale.append(client)
        for client in stale:
            self.disconnect(client)


live_clients = LiveClients()
