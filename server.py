import asyncio, json, itertools
import websockets
from websockets.exceptions import ConnectionClosed

# ---- in-memory database ----
USERS = {}                 # ws -> {"user_id": str}
SESSIONS = {}              # user_id -> set(ws)
CHATS = {}                 # chatId -> {"chatId": str, "name": str, "participants": set[str]}

next_chat_id = itertools.count(1)
next_msg_id = itertools.count(1)
next_evt_id = itertools.count(1)
seq_counter = itertools.count(1)

def ok(reqId, typ, data=None):
    return {"id": reqId, "type": typ, "status": "ok", "data": data or {}}
def err(reqId, typ, code, message):
    return {"id": reqId, "type": typ, "status": "error", "error": {"code": code, "message": message}}

async def send(ws, obj):
    try:
       await ws.send(json.dumps(obj))
    except ConnectionClosed:
        pass

async def push_event_to_user(user_id, typ, data):
    sessions = SESSIONS.get(user_id, set())
    if not sessions: return
    evt_id = f"s-{next(next_evt_id)}"
    seq    = next(seq_counter)
    evt = {"id": evt_id, "type": typ, "seq": seq, "data": data}
    await asyncio.gather(*(send(ws, evt) for ws in list(sessions)))


async def broadcast_to_chat(chat_id, typ, data):
    chat = CHATS.get(chat_id)
    if not chat: return
    await asyncio.gather(*(push_event_to_user(u, typ, data) for u in chat["participants"]))


# Accept both old (ws, path) and new (ws) signatures
async def handle(ws, path=None):
    USERS[ws] = {"user_id": None}
    try:
        async for message in ws:
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                continue

            typ   = msg.get("type")
            reqId = msg.get("id", "")
            data  = msg.get("data") or {}
            info  = USERS[ws]

            if typ == "auth":
                user_id = str(data.get("token", "")).strip()
                if not user_id:
                    await send(ws, err(reqId, "auth", "BAD_AUTH", "missing token")); continue
                info["user_id"] = user_id
                SESSIONS.setdefault(user_id, set()).add(ws)
                await send(ws, ok(reqId, "auth", {"ok": True, "userId": user_id}))

            elif typ == "createChat":   # <-- fixed name
                if not info["user_id"]:
                    await send(ws, err(reqId, "createChat", "UNAUTH", "login first")); continue
                me = info["user_id"]
                participants = set(data.get("participants", [])) | {me}
                name = (data.get("name") or "Chat").strip()
                chat_id = f"ch_{next(next_chat_id)}"
                CHATS[chat_id] = {"chatId": chat_id, "name": name, "participants": participants}
                await send(ws, ok(reqId, "createChat", {"chatId": chat_id}))
                await broadcast_to_chat(chat_id, "chatUpdate", {
                    "chatId": chat_id, "participants": sorted(list(participants)), "name": name
                })

            elif typ == "sendMessage":
                if not info["user_id"]:
                    await send(ws, err(reqId, "sendMessage", "UNAUTH", "login first")); continue
                chat_id = data.get("chatId")
                text    = data.get("message", "")
                chat    = CHATS.get(chat_id)
                if not chat:
                    await send(ws, err(reqId, "sendMessage", "NOT_FOUND", "no chat")); continue
                if info["user_id"] not in chat["participants"]:
                    await send(ws, err(reqId, "sendMessage", "NO_ACCESS", "not in chat")); continue
                message_id = f"m_{next(next_msg_id)}"
                await send(ws, ok(reqId, "sendMessage", {"status": "SUCCESS", "messageId": message_id}))
                await broadcast_to_chat(chat_id, "newMessage", {
                    "chatId": chat_id, "messageId": message_id, "userId": info["user_id"],
                    "text": text, "attachments": data.get("attachments", [])
                })

            elif typ == "ack":
                pass  # record acks if you implement resume later

            elif typ in ("ping", "pong"):
                pass

            else:
                await send(ws, err(reqId or "?", typ or "unknown", "BAD_TYPE", "unknown message type"))
    except ConnectionClosed:
        pass
    finally:
        info = USERS.pop(ws, None)
        if info and info["user_id"]:
            s = SESSIONS.get(info["user_id"])
            if s:
                s.discard(ws)
                if not s: SESSIONS.pop(info["user_id"], None)

async def main():
    print("Server on ws://localhost:8765/rt")
    async with websockets.serve(handle, "localhost", 8765, ping_interval=20, ping_timeout=20):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())