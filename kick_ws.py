import asyncio
import json
import websockets
import random
import aiohttp
import os

# Replace with your chatroom_id
CHATROOM_ID = os.getenv("CHATROOM_ID")

# Kick’s Pusher WebSocket endpoint
WS_URL = os.getenv("WS_URL")

# Your ntfy topic
NTFY_TOPIC = os.getenv("NTFY_TOPIC")

# Queue for ntfy
message_queue = asyncio.Queue()

# Track last message per user (for spam filtering)
last_message_by_user = {}


def split_message(text, limit=123):
    """Split message into chunks of up to `limit` chars."""
    if len(text) <= limit:
        return [text]

    parts = []
    for i in range(0, len(text), limit):
        parts.append(text[i:i + limit])

    total = len(parts)
    return [f"{part} [{i+1}/{total}]" for i, part in enumerate(parts)]


async def send_to_ntfy():
    """Worker that sends queued messages to ntfy with correct delay and order."""
    async with aiohttp.ClientSession() as session:
        first_message = True
        while True:
            user, text = await message_queue.get()

            if not first_message:
                await asyncio.sleep(5)
            else:
                first_message = False

            try:
                await session.post(
                    NTFY_TOPIC,
                    data=text.encode("utf-8"),
                    headers={"Title": user}
                )
            except Exception:
                pass  # suppress ntfy errors in log

            message_queue.task_done()

            if message_queue.empty():
                first_message = True


async def listen_chat(chatroom_id):
    async for ws in websockets.connect(WS_URL, ping_interval=20, ping_timeout=20):
        try:
            print("✅ Connected to Kick chat WebSocket")

            # Wait for connection established
            msg = await ws.recv()
            data = json.loads(msg)

            if data.get("event") == "pusher:connection_established":
                # Subscribe to chatroom
                subscribe_payload = {
                    "event": "pusher:subscribe",
                    "data": {
                        "auth": "",
                        "channel": f"chatrooms.{chatroom_id}.v2",
                    },
                }
                await ws.send(json.dumps(subscribe_payload))
                print(f"📡 Subscribed to chatroom {chatroom_id}")

            # Listen for messages
            async for message in ws:
                try:
                    event = json.loads(message)
                    if event.get("event") == "App\\Events\\ChatMessageEvent":
                        payload = json.loads(event["data"])
                        user = payload["sender"]["username"]
                        text = payload["content"]

                        # Spam filter: skip if same user sent same text
                        if last_message_by_user.get(user) == text:
                            continue
                        last_message_by_user[user] = text

                        # Print to console
                        print(f"[💬 {user}] {text}")

                        # Split if long
                        parts = split_message(text)

                        # Queue for ntfy
                        for part in parts:
                            await message_queue.put((user, part))

                except Exception:
                    pass  # suppress parsing errors in log

        except Exception:
            print("❌ Connection error, retrying...")
        finally:
            await asyncio.sleep(5)


async def main():
    worker = asyncio.create_task(send_to_ntfy())
    await listen_chat(CHATROOM_ID)
    await worker


if __name__ == "__main__":
    asyncio.run(main())
