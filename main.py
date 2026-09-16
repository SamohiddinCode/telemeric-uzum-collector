import asyncio
import base64
import hashlib
import html
import os
import secrets
import signal
from contextlib import asynccontextmanager
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Form, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.types import Channel, User
from telethon.utils import get_peer_id


API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
TARGET_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "-1002707306458"))
SITE_URL = os.environ.get("TELEMERIC_SITE_URL", "").rstrip("/")
COLLECTOR_SECRET = os.environ.get("TELEGRAM_COLLECTOR_SECRET", "")
SITES_AUTH_TOKEN = os.environ.get("SITES_AUTH_TOKEN", "")
SETUP_TOKEN = os.environ.get("SETUP_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", "5000"))

collector_task: asyncio.Task[Any] | None = None
backfill_task: asyncio.Task[Any] | None = None
collector_client: TelegramClient | None = None
login_client: TelegramClient | None = None
login_phone = ""
login_code_hash = ""
collector_status: dict[str, Any] = {
    "authorized": False,
    "connected": False,
    "account": "",
    "group": "",
    "error": "",
}
backfill_status: dict[str, Any] = {
    "running": False,
    "imported": 0,
    "error": "",
}


def required_settings_ready() -> bool:
    return bool(API_ID and API_HASH and SITE_URL and COLLECTOR_SECRET and SITES_AUTH_TOKEN and SETUP_TOKEN)


def site_headers() -> dict[str, str]:
    return {
        "authorization": f"Bearer {COLLECTOR_SECRET}",
        "OAI-Sites-Authorization": f"Bearer {SITES_AUTH_TOKEN}",
        "content-type": "application/json",
    }


def encrypt_session(session: str) -> str:
    key = hashlib.sha256(COLLECTOR_SECRET.encode("utf-8")).digest()
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, session.encode("utf-8"), b"telemeric-session-v1")
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_session(value: str) -> str:
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    key = hashlib.sha256(COLLECTOR_SECRET.encode("utf-8")).digest()
    return AESGCM(key).decrypt(raw[:12], raw[12:], b"telemeric-session-v1").decode("utf-8")


async def site_request(method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.request(method, f"{SITE_URL}{path}", headers=site_headers(), json=json)
        response.raise_for_status()
        return response.json()


async def load_session() -> str | None:
    payload = await site_request("GET", "/api/telegram/collector-session")
    encrypted = payload.get("session")
    return decrypt_session(encrypted) if encrypted else None


async def save_session(session: str) -> None:
    await site_request("PUT", "/api/telegram/collector-session", {"session": encrypt_session(session)})


async def sender_payload(message: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    sender = await message.get_sender()
    if isinstance(sender, User):
        return ({
            "id": int(sender.id),
            "first_name": sender.first_name or "",
            "last_name": sender.last_name or "",
            "username": sender.username or "",
            "is_bot": bool(sender.bot),
        }, None)
    if isinstance(sender, Channel):
        return (None, {
            "id": int(get_peer_id(sender)),
            "title": sender.title or "",
            "username": sender.username or "",
            "type": "channel",
        })
    sender_id = int(message.sender_id or 0)
    if sender_id:
        return ({"id": sender_id, "first_name": str(sender_id), "is_bot": False}, None)
    return (None, None)


async def normalize_message(message: Any) -> dict[str, Any] | None:
    text = (message.message or "").strip()
    if not text:
        return None
    sender, sender_chat = await sender_payload(message)
    if not sender and not sender_chat:
        return None
    body: dict[str, Any] = {
        "message_id": int(message.id),
        "date": int(message.date.timestamp()),
        "text": text,
        "chat": {"id": TARGET_CHAT_ID, "title": collector_status.get("group", ""), "type": "supergroup"},
    }
    if sender:
        body["from"] = sender
    if sender_chat:
        body["sender_chat"] = sender_chat
    if message.reply_to_msg_id:
        body["reply_to_message"] = {"message_id": int(message.reply_to_msg_id)}
    return {"update_id": int(message.id), "message": body}


async def deliver(updates: list[dict[str, Any]]) -> None:
    if not updates:
        return
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            await site_request("POST", "/api/telegram/ingest", {
                "updates": updates,
                "account": collector_status.get("account", "Telegram-аккаунт"),
                "groupTitle": collector_status.get("group", "Основная группа"),
            })
            return
        except Exception as exc:  # noqa: BLE001 - network retry boundary
            last_error = exc
            await asyncio.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"Не удалось передать данные: {last_error}")


async def heartbeat() -> None:
    while collector_status.get("connected"):
        try:
            await site_request("POST", "/api/telegram/ingest", {
                "updates": [],
                "account": collector_status.get("account", "Telegram-аккаунт"),
                "groupTitle": collector_status.get("group", "Основная группа"),
            })
            collector_status["error"] = ""
        except Exception as exc:  # noqa: BLE001 - status is shown to the operator
            collector_status["error"] = str(exc)
        await asyncio.sleep(30)


async def backfill(client: TelegramClient, entity: Any) -> int:
    messages = [message async for message in client.iter_messages(entity, limit=HISTORY_LIMIT)]
    messages.reverse()
    batch: list[dict[str, Any]] = []
    for message in messages:
        update = await normalize_message(message)
        if not update:
            continue
        batch.append(update)
        if len(batch) >= 100:
            await deliver(batch)
            batch = []
    await deliver(batch)
    return sum(1 for message in messages if (message.message or "").strip())


async def run_history_backfill(session: str) -> None:
    client = TelegramClient(StringSession(session), API_ID, API_HASH)
    backfill_status.update(running=True, imported=0, error="")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Требуется повторная авторизация Telegram-аккаунта")
        entity = await client.get_entity(TARGET_CHAT_ID)
        imported = await backfill(client, entity)
        backfill_status.update(imported=imported, error="")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced through health and setup page
        backfill_status["error"] = str(exc)
    finally:
        backfill_status["running"] = False
        if client.is_connected():
            await client.disconnect()


def start_history_backfill(session: str) -> None:
    global backfill_task
    if backfill_task and not backfill_task.done():
        return
    backfill_task = asyncio.create_task(run_history_backfill(session))


async def run_collector(session: str) -> None:
    global collector_client
    client = TelegramClient(StringSession(session), API_ID, API_HASH)
    collector_client = client
    try:
        await client.connect()
        if not await client.is_user_authorized():
            collector_status.update(authorized=False, connected=False, error="Требуется повторная авторизация")
            return
        me = await client.get_me()
        entity = await client.get_entity(TARGET_CHAT_ID)
        username = f"@{me.username}" if getattr(me, "username", None) else " ".join(
            item for item in [getattr(me, "first_name", ""), getattr(me, "last_name", "")] if item
        )
        collector_status.update(
            authorized=True,
            connected=True,
            account=username or "Telegram-аккаунт",
            group=getattr(entity, "title", "Основная группа"),
            error="",
        )

        @client.on(events.NewMessage(chats=entity))
        async def on_new_message(event: Any) -> None:
            update = await normalize_message(event.message)
            if update:
                await deliver([update])

        heartbeat_task = asyncio.create_task(heartbeat())
        await backfill(client, entity)
        await client.run_until_disconnected()
        heartbeat_task.cancel()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - keep health endpoint alive for diagnosis
        collector_status.update(connected=False, error=str(exc))
    finally:
        collector_status["connected"] = False
        if client.is_connected():
            await client.disconnect()


async def start_saved_collector() -> None:
    global collector_task
    if collector_task and not collector_task.done():
        return
    if not required_settings_ready():
        collector_status["error"] = "Не заполнены обязательные настройки"
        return
    try:
        session = await load_session()
    except Exception as exc:  # noqa: BLE001 - surfaced via health
        collector_status["error"] = f"Не удалось получить сессию: {exc}"
        return
    if not session:
        collector_status["error"] = "Откройте /setup и войдите в Telegram"
        return
    collector_task = asyncio.create_task(run_collector(session))


def require_setup_token(token: str) -> None:
    if not SETUP_TOKEN or token != SETUP_TOKEN:
        raise HTTPException(status_code=401, detail="Неверный ключ настройки")


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>body{{margin:0;background:#f5f4fb;color:#171b2e;font:16px system-ui}}main{{max-width:520px;margin:8vh auto;padding:32px;background:#fff;border:1px solid #e6e2f1;border-radius:24px;box-shadow:0 18px 60px #49208018}}h1{{margin:0 0 8px}}p{{color:#667085;line-height:1.55}}label{{display:block;margin:18px 0 6px;font-weight:700}}input{{width:100%;box-sizing:border-box;padding:13px;border:1px solid #d8d2e6;border-radius:12px;font:inherit}}button{{width:100%;margin-top:22px;padding:14px;border:0;border-radius:12px;background:#7c3aed;color:#fff;font:700 16px system-ui;cursor:pointer}}.ok{{padding:14px;background:#eafaf3;color:#107458;border-radius:12px}}.error{{padding:14px;background:#fff4ea;color:#a34c00;border-radius:12px}}</style></head><body><main>{body}</main></body></html>""")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if TELEGRAM_BOT_TOKEN:
        collector_status.update(
            authorized=True,
            connected=False,
            account="@uzum_franchise_support_bot",
            group="Uzum Franchise Chat",
            error="Ожидание связи с bridge",
        )
    else:
        await start_saved_collector()
    yield
    global collector_task
    if collector_task:
        collector_task.cancel()
    if collector_client and collector_client.is_connected():
        await collector_client.disconnect()
    if backfill_task:
        backfill_task.cancel()


app = FastAPI(title="Telemeric Uzum Collector", lifespan=lifespan)


def require_bridge_token(authorization: str) -> None:
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization.startswith(prefix) else ""
    if not TELEGRAM_BOT_TOKEN or not secrets.compare_digest(supplied, TELEGRAM_BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Неверный ключ bridge")


@app.post("/bridge/ingest")
async def bridge_ingest(
    payload: dict[str, Any],
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_bridge_token(authorization)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    account = str(payload.get("account") or "@uzum_franchise_support_bot")
    group_title = str(payload.get("groupTitle") or "Uzum Franchise Chat")
    collector_status.update(
        authorized=True,
        connected=True,
        account=account,
        group=group_title,
        error="",
    )
    if updates:
        await deliver(updates)
    else:
        await site_request("POST", "/api/telegram/ingest", {
            "updates": [],
            "account": account,
            "groupTitle": group_title,
        })
    return {"ok": True, "accepted": len(updates)}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "settingsReady": required_settings_ready(),
        **collector_status,
        "historyImport": backfill_status,
    }


@app.get("/setup", response_class=HTMLResponse)
async def setup(token: str = Query(default="")) -> HTMLResponse:
    require_setup_token(token)
    if collector_status.get("connected") and not TELEGRAM_BOT_TOKEN:
        return page("Telemeric Uzum", f"<h1>Telegram подключён</h1><p class='ok'>{html.escape(str(collector_status.get('account')))} читает группу {html.escape(str(collector_status.get('group')))}.</p>")
    if backfill_status["running"]:
        return page("Импорт истории", "<h1>История загружается</h1><p class='ok'>Можно закрыть страницу. Импорт продолжится в фоне.</p>")
    description = "Введите номер аккаунта, который уже состоит в нужной группе. Он будет использован только для однократной загрузки старой истории." if TELEGRAM_BOT_TOKEN else "Введите номер аккаунта, который уже состоит в нужной группе. Права администратора не нужны."
    return page("Подключение Telegram", f"<h1>Импорт истории Telegram</h1><p>{html.escape(description)}</p><form method='post' action='/setup/send-code'><input type='hidden' name='token' value='{html.escape(token)}'><label>Номер телефона</label><input name='phone' type='tel' placeholder='+998901234567' required><button type='submit'>Получить код в Telegram</button></form>")


@app.post("/setup/send-code", response_class=HTMLResponse)
async def send_code(token: str = Form(...), phone: str = Form(...)) -> HTMLResponse:
    global login_client, login_phone, login_code_hash
    require_setup_token(token)
    if not API_ID or not API_HASH:
        return page("Нет API-настроек", "<h1>Нужны Telegram API ID и API Hash</h1><p class='error'>Сначала заполните секреты сервиса в Render.</p>")
    if login_client and login_client.is_connected():
        await login_client.disconnect()
    login_client = TelegramClient(StringSession(), API_ID, API_HASH)
    await login_client.connect()
    sent = await login_client.send_code_request(phone)
    login_phone = phone
    login_code_hash = sent.phone_code_hash
    return page("Введите код", f"<h1>Введите код из Telegram</h1><p>Код отправлен на {html.escape(phone)}. Если включён облачный пароль, укажите его ниже.</p><form method='post' action='/setup/verify'><input type='hidden' name='token' value='{html.escape(token)}'><label>Код</label><input name='code' inputmode='numeric' autocomplete='one-time-code' required><label>Облачный пароль (если есть)</label><input name='password' type='password'><button type='submit'>Подключить аккаунт</button></form>")


@app.post("/setup/verify", response_class=HTMLResponse)
async def verify(token: str = Form(...), code: str = Form(...), password: str = Form(default="")) -> HTMLResponse:
    global login_client
    require_setup_token(token)
    if not login_client or not login_client.is_connected() or not login_phone or not login_code_hash:
        return page("Сессия истекла", f"<h1>Начните заново</h1><p class='error'>Запрос кода истёк.</p><a href='/setup?token={html.escape(token)}'>Вернуться к подключению</a>")
    try:
        await login_client.sign_in(phone=login_phone, code=code, phone_code_hash=login_code_hash)
    except SessionPasswordNeededError:
        if not password:
            return page("Нужен пароль", "<h1>Введите облачный пароль</h1><p class='error'>Для аккаунта включена двухэтапная аутентификация. Вернитесь назад и заполните поле пароля.</p>")
        await login_client.sign_in(password=password)
    session = login_client.session.save()
    await save_session(session)
    await login_client.disconnect()
    login_client = None
    if TELEGRAM_BOT_TOKEN:
        start_history_backfill(session)
        return page("Готово", f"<h1>Импорт запущен</h1><p class='ok'>Загружаются последние {HISTORY_LIMIT} сообщений группы. Новые сообщения продолжает получать рабочий бот.</p>")
    await start_saved_collector()
    return page("Готово", "<h1>Аккаунт подключён</h1><p class='ok'>Сборщик загружает историю и новые сообщения выбранной группы. Кабинет начнёт обновляться автоматически.</p>")


def stop_loop(*_: Any) -> None:
    if collector_task:
        collector_task.cancel()


signal.signal(signal.SIGTERM, stop_loop)
