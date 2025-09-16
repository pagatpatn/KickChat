import asyncio
import json
import websockets
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
    """Worker that sends queued messages to ntfy with strict FIFO and delay."""
    async with aiohttp.ClientSession() as session:
        while True:
            user, text = await message_queue.get()

            try:
                await session.post(
                    NTFY_TOPIC,
                    data=text.encode("utf-8"),
                    headers={"Title": user}
                )
            except Exception:
                pass  # suppress ntfy errors in log

            message_queue.task_done()

            # Only delay if there are more messages waiting
            if not message_queue.empty():
                await asyncio.sleep(5)


async def handle_event(event):
    """Parse different Kick events and enqueue them for ntfy."""
    event_type = event.get("event")
    data = json.loads(event.get("data", "{}"))

    if event_type == "App\\Events\\ChatMessageEvent":
        user = data["sender"]["username"]
        text = data["content"]

        # Spam filter
        if last_message_by_user.get(user) == text:
            return
        last_message_by_user[user] = text

        # Split if long
        parts = split_message(text)
        for part in parts:
            await message_queue.put((user, part))

        print(f"[💬 {user}] {text}")

    elif event_type == "App\\Events\\SubscriptionEvent":
        user = data["user"]["username"]
        months = data.get("months", 1)
        msg = f"🎉 Subscribed for {months} month(s)!"
        await message_queue.put((user, msg))
        print(f"[⭐ SUB] {user} → {msg}")

    elif event_type == "App\\Events\\GiftedSubEvent":
        gifter = data["gifter"]["username"]
        amount = data.get("gift_count", 1)
        msg = f"🎁 Gifted {amount} sub(s)!"
        await message_queue.put((gifter, msg))
        print(f"[🎁 GIFT] {gifter} → {msg}")

    elif event_type == "App\\Events\\TipEvent":
        user = data["sender"]["username"]
        amount = data.get("amount", 0)
        currency = data.get("currency", "USD")
        msg = f"💸 Tipped {amount} {currency}"
        await message_queue.put((user, msg))
        print(f"[💸 TIP] {user} → {msg}")

    elif event_type == "App\\Events\\RaidEvent":
        user = data["raider"]["username"]
        viewers = data.get("viewer_count", 0)
        msg = f"⚡ Raided with {viewers} viewers!"
        await message_queue.put((user, msg))
        print(f"[⚡ RAID] {user} → {msg}")

    elif event_type == "App\\Events\\StickerEvent":
        user = data["sender"]["username"]
        sticker = data["sticker"]["name"]
        msg = f"🌟 Sent sticker: {sticker}"
        await message_queue.put((user, msg))
        print(f"[🌟 STICKER] {user} → {msg}")


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
                    await handle_event(event)
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
