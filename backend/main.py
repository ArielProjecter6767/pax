import os
import re
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pathlib import Path
from fastapi.responses import FileResponse, HTMLResponse
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr, Field
from pymongo import MongoClient, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

APP_ENV = os.getenv("APP_ENV", "development")
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "pax").strip() or "pax"
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
JWT_ALGORITHM = "HS256"
TOKEN_HOURS = 24 * 14

if not JWT_SECRET:
    if APP_ENV == "production":
        raise RuntimeError("JWT_SECRET must be configured in production.")
    JWT_SECRET = secrets.token_urlsafe(48)

app = FastAPI(title="PAX API", version="0.1.0")
origins_raw = os.getenv("CORS_ORIGINS", "*")
origins = ["*"] if origins_raw.strip() == "*" else [x.strip() for x in origins_raw.split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = None
db = None
if MONGODB_URI:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000)
    db = client[MONGODB_DB]
    try:
        db.users.create_index("username_lower", unique=True)
        db.users.create_index("email_lower", unique=True)
        db.messages.create_index([("conversation_id", 1), ("created_at", -1)])
        db.messages.create_index([("sender_id", 1), ("recipient_id", 1), ("created_at", -1)])
    except PyMongoError:
        # The health endpoint remains useful even if Atlas is temporarily unreachable.
        pass

security = HTTPBearer(auto_error=False)


def database():
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured. Set MONGODB_URI in Render.")
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(status_code=503, detail="Database is temporarily unavailable.")
    return db


def now_utc():
    return datetime.now(timezone.utc)


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split("$", 1)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 310_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def make_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": now_utc() + timedelta(hours=TOKEN_HOURS), "iat": now_utc()},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def public_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "display_name": user.get("display_name", user["username"]),
        "bio": user.get("bio", ""),
        "created_at": user.get("created_at", now_utc()).isoformat(),
    }


def user_from_token(token: str, database_obj=None):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("Missing subject")
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired access token.")
    d = database_obj if database_obj is not None else database()
    from bson import ObjectId
    try:
        user = d.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Account not found.")
    return user


def current_user(credentials: HTTPAuthorizationCredentials = Depends(security), d=Depends(database)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authorization token required.")
    return user_from_token(credentials.credentials, d)


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=24, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=48)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginInput(BaseModel):
    login: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class MessageInput(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


class GroupCreateInput(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    member_ids: list[str] = Field(default_factory=list, max_length=99)


class GroupMessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class MessageEditInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ForwardMessageInput(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=64)


class TranslateInput(BaseModel):
    target_language: str = Field(default="en", min_length=2, max_length=12)


class ProfileInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=48)
    bio: str = Field(default="", max_length=240)


class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, set[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket):
        sockets = self.connections.get(user_id)
        if sockets:
            sockets.discard(websocket)
            if not sockets:
                self.connections.pop(user_id, None)

    async def send_user(self, user_id: str, payload: dict):
        stale = []
        for socket in list(self.connections.get(user_id, set())):
            try:
                await socket.send_json(payload)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.disconnect(user_id, socket)


manager = ConnectionManager()



PREVIEW_PAGE = Path(__file__).resolve().with_name("preview.html")
PREVIEW_WINDOWS_DIR = Path(__file__).resolve().parent.parent / "windows"


@app.get("/preview", include_in_schema=False)
@app.get("/preview/", include_in_schema=False)
def preview_page():
    if not PREVIEW_PAGE.is_file():
        raise HTTPException(status_code=404, detail="Preview page is not installed.")
    return FileResponse(
        str(PREVIEW_PAGE),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/preview/windows/", include_in_schema=False)
def preview_windows_page():
    page = PREVIEW_WINDOWS_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Windows preview files are missing.")
    html = page.read_text(encoding="utf-8")
    html = html.replace('href="styles.css"', 'href="/preview/windows/styles.css"')
    html = html.replace('src="renderer.js"', 'src="/preview/windows/renderer.js"')
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/preview/windows/{asset_name}", include_in_schema=False)
def preview_windows_asset(asset_name: str):
    allowed_assets = {
        "styles.css": (PREVIEW_WINDOWS_DIR / "styles.css", "text/css"),
        "renderer.js": (PREVIEW_WINDOWS_DIR / "renderer.js", "text/javascript"),
    }
    asset = allowed_assets.get(asset_name)
    if asset is None or not asset[0].is_file():
        raise HTTPException(status_code=404, detail="Preview asset not found.")
    return FileResponse(
        str(asset[0]),
        media_type=asset[1],
        headers={"Cache-Control": "no-store"},
    )


@app.get("/")
def root():
    return {"app": "PAX", "status": "ok", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    result = {"status": "ok", "database": "not_configured"}
    if db is not None:
        try:
            client.admin.command("ping")
            result["database"] = "connected"
        except PyMongoError:
            result["status"] = "degraded"
            result["database"] = "unavailable"
    return result


@app.post("/api/auth/register")
def register(body: RegisterInput, d=Depends(database)):
    username = body.username.strip()
    email = str(body.email).strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", username):
        raise HTTPException(status_code=422, detail="Username must be 3–24 characters: letters, numbers, dot, underscore or hyphen.")
    doc = {
        "username": username,
        "username_lower": username.lower(),
        "display_name": body.display_name.strip(),
        "email": email,
        "email_lower": email,
        "password_hash": hash_password(body.password),
        "bio": "",
        "created_at": now_utc(),
    }
    try:
        result = d.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="That username or email is already registered.")
    doc["_id"] = result.inserted_id
    return {"access_token": make_token(str(result.inserted_id)), "token_type": "bearer", "user": public_user(doc)}


@app.post("/api/auth/login")
def login(body: LoginInput, d=Depends(database)):
    value = body.login.strip().lower()
    user = d.users.find_one({"$or": [{"username_lower": value}, {"email_lower": value}]})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect username/email or password.")
    return {"access_token": make_token(str(user["_id"])), "token_type": "bearer", "user": public_user(user)}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return public_user(user)


@app.patch("/api/profile")
def update_profile(body: ProfileInput, user=Depends(current_user), d=Depends(database)):
    d.users.update_one({"_id": user["_id"]}, {"$set": {"display_name": body.display_name.strip(), "bio": body.bio.strip()}})
    user["display_name"] = body.display_name.strip()
    user["bio"] = body.bio.strip()
    return public_user(user)


@app.get("/api/users")
def search_users(q: str = Query(default="", max_length=40), user=Depends(current_user), d=Depends(database)):
    query = q.strip().lower()
    criteria = {"_id": {"$ne": user["_id"]}}
    if query:
        escaped = re.escape(query)
        criteria["$or"] = [
            {"username_lower": {"$regex": escaped}},
            {"display_name": {"$regex": escaped, "$options": "i"}},
        ]
    cursor = d.users.find(criteria).sort("username_lower", 1).limit(100)
    return [public_user(item) for item in cursor]


def conversation_id(user_a: str, user_b: str) -> str:
    return ":".join(sorted([user_a, user_b]))


@app.get("/api/conversations")
def conversations(user=Depends(current_user), d=Depends(database)):
    uid = str(user["_id"])
    rows = list(d.messages.aggregate([
        {"$match": {"$or": [{"sender_id": uid}, {"recipient_id": uid}]}},
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$conversation_id", "last_message": {"$first": "$$ROOT"}}},
        {"$sort": {"last_message.created_at": -1}},
        {"$limit": 100},
    ]))
    result = []
    from bson import ObjectId
    for row in rows:
        msg = row["last_message"]
        other_id = msg["recipient_id"] if msg["sender_id"] == uid else msg["sender_id"]
        try:
            other = d.users.find_one({"_id": ObjectId(other_id)})
        except Exception:
            other = None
        if other:
            result.append({
                "id": row["_id"],
                "user": public_user(other),
                "last_message": {
                    "text": msg["text"],
                    "sender_id": msg["sender_id"],
                    "created_at": msg["created_at"].isoformat(),
                },
            })
    return result


@app.get("/api/messages/{other_user_id}")
def get_messages(other_user_id: str, limit: int = Query(default=100, ge=1, le=200), user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    try:
        other = d.users.find_one({"_id": ObjectId(other_user_id)})
    except Exception:
        other = None
    if not other:
        raise HTTPException(status_code=404, detail="User not found.")
    uid = str(user["_id"])
    cid = conversation_id(uid, other_user_id)
    docs = list(d.messages.find({"conversation_id": cid}).sort("created_at", DESCENDING).limit(limit))
    docs.reverse()
    return [{
        "id": str(m["_id"]),
        "conversation_id": m["conversation_id"],
        "sender_id": m["sender_id"],
        "recipient_id": m["recipient_id"],
        "text": m["text"],
        "created_at": m["created_at"].isoformat(),
    } for m in docs]


@app.post("/api/messages")
async def send_message(body: MessageInput, user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    try:
        recipient = d.users.find_one({"_id": ObjectId(body.recipient_id)})
    except Exception:
        recipient = None
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found.")
    uid = str(user["_id"])
    if uid == body.recipient_id:
        raise HTTPException(status_code=400, detail="You cannot message yourself.")
    clean_text = body.text.strip()
    if not clean_text:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    doc = {
        "conversation_id": conversation_id(uid, body.recipient_id),
        "sender_id": uid,
        "recipient_id": body.recipient_id,
        "text": clean_text,
        "created_at": now_utc(),
    }
    inserted = d.messages.insert_one(doc)
    payload = {
        "id": str(inserted.inserted_id),
        "conversation_id": doc["conversation_id"],
        "sender_id": uid,
        "recipient_id": body.recipient_id,
        "text": clean_text,
        "created_at": doc["created_at"].isoformat(),
    }
    await manager.send_user(body.recipient_id, {"type": "message", "message": payload})
    await manager.send_user(uid, {"type": "message", "message": payload})
    return payload




def public_message(m: dict) -> dict:
    return {
        "id": str(m["_id"]),
        "conversation_id": m.get("conversation_id"),
        "group_id": m.get("group_id"),
        "sender_id": m["sender_id"],
        "recipient_id": m.get("recipient_id"),
        "text": m.get("text", ""),
        "created_at": m["created_at"].isoformat(),
        "edited_at": m["edited_at"].isoformat() if m.get("edited_at") else None,
        "deleted": bool(m.get("deleted", False)),
        "forwarded": bool(m.get("forwarded_from")),
    }


def require_group_member(group: dict, user_id: str):
    if not group or user_id not in group.get("member_ids", []):
        raise HTTPException(status_code=404, detail="Group not found.")
    return group


@app.get("/api/groups")
def list_groups(user=Depends(current_user), d=Depends(database)):
    uid = str(user["_id"])
    result = []
    for g in d.groups.find({"member_ids": uid}).sort("created_at", DESCENDING).limit(100):
        result.append({
            "id": str(g["_id"]), "name": g["name"],
            "member_ids": g.get("member_ids", []),
            "created_by": g["created_by"],
            "created_at": g["created_at"].isoformat(),
        })
    return result


@app.post("/api/groups")
async def create_group(body: GroupCreateInput, user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    uid = str(user["_id"])
    members = list(dict.fromkeys([uid] + [str(x) for x in body.member_ids if str(x) != uid]))
    if len(members) < 2:
        raise HTTPException(status_code=422, detail="Select at least one other member.")
    if len(members) > 100:
        raise HTTPException(status_code=422, detail="A group can contain at most 100 members.")
    for member_id in members:
        try:
            found = d.users.find_one({"_id": ObjectId(member_id)}, {"_id": 1})
        except Exception:
            found = None
        if not found:
            raise HTTPException(status_code=422, detail="One or more group members do not exist.")
    doc = {"name": body.name.strip(), "member_ids": members, "created_by": uid, "created_at": now_utc()}
    inserted = d.groups.insert_one(doc)
    group_id = str(inserted.inserted_id)
    result = {"id": group_id, "name": doc["name"], "member_ids": members, "created_by": uid, "created_at": doc["created_at"].isoformat()}
    for member_id in members:
        await manager.send_user(member_id, {"type": "group_created", "group": result})
    return result


@app.get("/api/groups/{group_id}/messages")
def get_group_messages(group_id: str, limit: int = Query(default=100, ge=1, le=200), user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    try:
        group = d.groups.find_one({"_id": ObjectId(group_id)})
    except Exception:
        group = None
    require_group_member(group, str(user["_id"]))
    docs = list(d.messages.find({"group_id": group_id}).sort("created_at", DESCENDING).limit(limit))
    docs.reverse()
    return [public_message(m) for m in docs]


@app.post("/api/groups/{group_id}/messages")
async def send_group_message(group_id: str, body: GroupMessageInput, user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    uid = str(user["_id"])
    try:
        group = d.groups.find_one({"_id": ObjectId(group_id)})
    except Exception:
        group = None
    require_group_member(group, uid)
    clean_text = body.text.strip()
    if not clean_text:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    doc = {"group_id": group_id, "sender_id": uid, "text": clean_text, "created_at": now_utc(), "deleted": False}
    inserted = d.messages.insert_one(doc)
    doc["_id"] = inserted.inserted_id
    payload = public_message(doc)
    for member_id in group.get("member_ids", []):
        await manager.send_user(member_id, {"type": "group_message", "message": payload})
    return payload


@app.patch("/api/messages/{message_id}")
async def edit_message(message_id: str, body: MessageEditInput, user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    try:
        oid = ObjectId(message_id)
        msg = d.messages.find_one({"_id": oid})
    except Exception:
        msg = None
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found.")
    if msg.get("sender_id") != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You can only edit your own messages.")
    if msg.get("deleted"):
        raise HTTPException(status_code=409, detail="Deleted messages cannot be edited.")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    edited_at = now_utc()
    d.messages.update_one({"_id": oid}, {"$set": {"text": text, "edited_at": edited_at}})
    msg.update({"text": text, "edited_at": edited_at})
    payload = public_message(msg)
    recipients = msg.get("member_ids", []) if msg.get("group_id") else [msg.get("recipient_id"), msg.get("sender_id")]
    if msg.get("group_id"):
        group = d.groups.find_one({"_id": ObjectId(msg["group_id"])})
        recipients = group.get("member_ids", []) if group else []
    for rid in set(x for x in recipients if x):
        await manager.send_user(rid, {"type": "message_updated", "message": payload})
    return payload


@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: str, user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    try:
        oid = ObjectId(message_id)
        msg = d.messages.find_one({"_id": oid})
    except Exception:
        msg = None
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found.")
    if msg.get("sender_id") != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You can only delete your own messages.")
    d.messages.update_one({"_id": oid}, {"$set": {"text": "", "deleted": True, "deleted_at": now_utc()}})
    msg.update({"text": "", "deleted": True})
    payload = public_message(msg)
    recipients = [msg.get("recipient_id"), msg.get("sender_id")]
    if msg.get("group_id"):
        group = d.groups.find_one({"_id": ObjectId(msg["group_id"])})
        recipients = group.get("member_ids", []) if group else []
    for rid in set(x for x in recipients if x):
        await manager.send_user(rid, {"type": "message_updated", "message": payload})
    return {"ok": True, "id": message_id}


@app.post("/api/messages/{message_id}/forward")
async def forward_message(message_id: str, body: ForwardMessageInput, user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    uid = str(user["_id"])
    try:
        original = d.messages.find_one({"_id": ObjectId(message_id)})
        recipient = d.users.find_one({"_id": ObjectId(body.recipient_id)})
    except Exception:
        original, recipient = None, None
    if not original or original.get("deleted"):
        raise HTTPException(status_code=404, detail="Message not found.")
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found.")
    if body.recipient_id == uid:
        raise HTTPException(status_code=400, detail="You cannot forward a message to yourself.")
    allowed = original.get("sender_id") == uid or original.get("recipient_id") == uid
    if original.get("group_id"):
        group = d.groups.find_one({"_id": ObjectId(original["group_id"])})
        allowed = bool(group and uid in group.get("member_ids", []))
    if not allowed:
        raise HTTPException(status_code=403, detail="You cannot forward this message.")
    doc = {"conversation_id": conversation_id(uid, body.recipient_id), "sender_id": uid, "recipient_id": body.recipient_id, "text": original["text"], "created_at": now_utc(), "forwarded_from": message_id}
    inserted = d.messages.insert_one(doc)
    doc["_id"] = inserted.inserted_id
    payload = public_message(doc)
    await manager.send_user(body.recipient_id, {"type": "message", "message": payload})
    await manager.send_user(uid, {"type": "message", "message": payload})
    return payload


@app.post("/api/messages/{message_id}/translate")
async def translate_message(message_id: str, body: TranslateInput, user=Depends(current_user), d=Depends(database)):
    from bson import ObjectId
    try:
        msg = d.messages.find_one({"_id": ObjectId(message_id)})
    except Exception:
        msg = None
    if not msg or msg.get("deleted"):
        raise HTTPException(status_code=404, detail="Message not found.")
    uid = str(user["_id"])
    allowed = msg.get("sender_id") == uid or msg.get("recipient_id") == uid
    if msg.get("group_id"):
        try:
            group = d.groups.find_one({"_id": ObjectId(msg["group_id"])})
        except Exception:
            group = None
        allowed = bool(group and uid in group.get("member_ids", []))
    if not allowed:
        raise HTTPException(status_code=403, detail="You cannot translate this message.")
    translate_url = os.getenv("LIBRETRANSLATE_URL", "").strip().rstrip("/")
    if not translate_url:
        raise HTTPException(status_code=503, detail="Translation is not configured. Set LIBRETRANSLATE_URL in Render.")
    import urllib.request
    import json
    payload = json.dumps({"q": msg["text"], "source": "auto", "target": body.target_language, "format": "text"}).encode("utf-8")
    req = urllib.request.Request(translate_url + "/translate", data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            translated = json.loads(response.read().decode("utf-8")).get("translatedText")
    except Exception:
        raise HTTPException(status_code=502, detail="Translation service is temporarily unavailable.")
    if not translated:
        raise HTTPException(status_code=502, detail="Translation service returned no text.")
    return {"message_id": message_id, "target_language": body.target_language, "translated_text": translated}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default="")):
    if not token:
        await websocket.close(code=4401)
        return
    try:
        user = user_from_token(token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    uid = str(user["_id"])
    await manager.connect(uid, websocket)
    try:
        await websocket.send_json({"type": "connected", "user_id": uid})
        while True:
            # Client may send ping frames as JSON; messages are sent via authenticated REST.
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "call_signal":
                target_id = str(data.get("to", "")).strip()
                signal = data.get("signal")
                if target_id and target_id != uid and isinstance(signal, dict):
                    await manager.send_user(target_id, {
                        "type": "call_signal", "from": uid,
                        "call_id": str(data.get("call_id", ""))[:100],
                        "media": "video" if data.get("media") == "video" else "audio",
                        "signal": signal,
                    })
            elif data.get("type") == "group_call_signal":
                group_id = str(data.get("group_id", "")).strip()
                signal = data.get("signal")
                if group_id and isinstance(signal, dict):
                    from bson import ObjectId
                    try:
                        group = db.groups.find_one({"_id": ObjectId(group_id)}) if db is not None else None
                    except Exception:
                        group = None
                    if group and uid in group.get("member_ids", []):
                        for member_id in group["member_ids"]:
                            if member_id != uid:
                                await manager.send_user(member_id, {
                                    "type": "group_call_signal", "from": uid,
                                    "group_id": group_id,
                                    "call_id": str(data.get("call_id", ""))[:100],
                                    "media": "video" if data.get("media") == "video" else "audio",
                                    "signal": signal,
                                })
    except WebSocketDisconnect:
        manager.disconnect(uid, websocket)
    except Exception:
        manager.disconnect(uid, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
