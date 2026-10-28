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
    d = database_obj or database()
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
            if isinstance(data, dict) and data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(uid, websocket)
    except Exception:
        manager.disconnect(uid, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
