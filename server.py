"""
OCR Live Stream Server - FastAPI backend.
Gerencia salas, streams e extração OCR em tempo real.
"""

import asyncio
import json
import uuid
import time
import os
import io
import httpx
import logging
import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from stream_manager import StreamManager

# OCR Engine selection: 'paddle' (faster) or 'easyocr' (legacy)
OCR_ENGINE = os.environ.get("OCR_ENGINE", "paddle").lower()
if OCR_ENGINE == "easyocr":
    from ocr_extractor import extract_from_bytes
else:
    from ocr_extractor_paddle import extract_from_bytes

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# Database
# ============================================================
DB_PATH = os.environ.get("DB_PATH", "ocr_rooms.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                stream_url TEXT,
                auction_id TEXT,
                ocr_interval REAL DEFAULT 1.0,
                status TEXT DEFAULT 'idle',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        # Migration: add auction_id if missing
        try:
            await db.execute("ALTER TABLE rooms ADD COLUMN auction_id TEXT")
        except:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS regions (
                id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL,
                type TEXT NOT NULL,
                label TEXT,
                value TEXT,
                x REAL NOT NULL,
                y REAL NOT NULL,
                width REAL NOT NULL,
                height REAL NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
            )
        """)
        # Migration: add value if missing
        try:
            await db.execute("ALTER TABLE regions ADD COLUMN value TEXT")
        except:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS extractions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                data TEXT NOT NULL,
                extracted_at TEXT,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lot_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                lot_number TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                final_value TEXT,
                bid_count INTEGER DEFAULT 0,
                extra_data TEXT,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bid_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                lot_number TEXT NOT NULL,
                value TEXT NOT NULL,
                payload TEXT,
                captured_at TEXT,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS post_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                old_lot TEXT NOT NULL,
                new_lot TEXT NOT NULL,
                bid_count INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS region_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                regions TEXT NOT NULL,
                created_at TEXT
            )
        """)
        await db.commit()


# ============================================================
# In-memory state (streams + websockets)
# ============================================================
active_streams: dict[str, StreamManager] = {}
ocr_tasks: dict[str, asyncio.Task] = {}
ws_connections: dict[str, list[WebSocket]] = {}
last_results: dict[str, dict] = {}  # Último resultado OCR por sala (para detectar mudanças)
lot_state: dict[str, dict] = {}    # Estado do lote por sala: {lot, value, bid_count, started_at}


# ============================================================
# Telegram Alerts
# ============================================================
TELEGRAM_BOT_TOKEN = "8786862360:AAHZluHj24ze5bksPVZ9l1bkkEqYk2Vdk50"
TELEGRAM_CHAT_ID = 1713046621
telegram_last_alert: dict[str, float] = {}  # Cooldown por sala (evita spam)
TELEGRAM_COOLDOWN = 300  # 5 minutos entre alertas da mesma sala


async def send_telegram_alert(message: str, room_id: str = ""):
    """Envia alerta para o Telegram com cooldown por sala."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    # Cooldown: não enviar mais de 1 alerta a cada 5 min por sala
    now = time.time()
    if room_id and room_id in telegram_last_alert:
        if now - telegram_last_alert[room_id] < TELEGRAM_COOLDOWN:
            return

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🚨 RemateWeb OCR\n\n{message}", "parse_mode": "HTML"}
            )
        telegram_last_alert[room_id] = now
        logger.info(f"[TELEGRAM] Alerta enviado: {message}")
    except Exception as e:
        logger.error(f"[TELEGRAM] Erro ao enviar: {e}")


# ============================================================
# RemateWeb API Integration
# ============================================================
REMATEWEB_API_URL = "https://test.api-net9.remateweb.com/api/ocr/bid"
REMATEWEB_API_KEY = "0c18ab41-eb23-4782-8e3f-34582fad10b6"
remateweb_last_sent: dict[str, dict] = {}  # Último payload enviado por sala


async def post_ocr_bid(room_id: str, auction_id: int, lote: str, valor: str):
    """Envia dados OCR para a API RemateWeb."""
    # Se lote E valor forem "0", significa que não tem lote em pista
    if lote == "0" and valor == "0":
        lot_number = None
        value = 0
    else:
        lot_number = lote if lote and lote != "0" else None
        # Limpar valor: remover pontos e converter para float
        try:
            value = float(valor.replace(".", "").replace(",", ".")) if valor and valor != "0" else 0
        except ValueError:
            value = 0

    payload = {
        "apiKey": REMATEWEB_API_KEY,
        "auctionId": auction_id,
        "lotNumber": lot_number,
        "value": value
    }

    # Evitar enviar payload duplicado
    last = remateweb_last_sent.get(room_id)
    if last and last.get("lotNumber") == lot_number and last.get("value") == value:
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(REMATEWEB_API_URL, json=payload)
            logger.info(f"[REMATEWEB] POST {payload} → {resp.status_code}")
            remateweb_last_sent[room_id] = payload
    except Exception as e:
        logger.error(f"[REMATEWEB] Erro ao enviar: {e}")


# ============================================================
# App lifecycle
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    os.makedirs("frames", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    yield
    # Cleanup
    for stream in active_streams.values():
        stream.stop()
    for task in ocr_tasks.values():
        task.cancel()


app = FastAPI(title="OCR Live Stream", lifespan=lifespan)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ============================================================
# Models
# ============================================================
class CreateRoomRequest(BaseModel):
    name: str


class SetStreamRequest(BaseModel):
    stream_url: str


class SetRegionsRequest(BaseModel):
    regions: list[dict]


class UpdateIntervalRequest(BaseModel):
    interval: float


# ============================================================
# Routes - Pages
# ============================================================
@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


@app.get("/")
async def home():
    return FileResponse("static/index.html")


@app.get("/room/{room_id}")
async def room_page(room_id: str):
    return FileResponse("static/room.html")


# ============================================================
# Auth
# ============================================================
REMATEWEB_API = "https://test.api-net9.remateweb.com"


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    """Proxy de login: autentica via RemateWeb API e verifica se é admin via JWT."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Obter token
            token_resp = await client.post(
                f"{REMATEWEB_API}/token",
                data={"grant_type": "password", "username": req.username, "password": req.password}
            )
            logger.info(f"[AUTH] /token status={token_resp.status_code}")
            if token_resp.status_code != 200:
                try:
                    err = token_resp.json()
                    msg = err.get("error_description") or err.get("error") or "Usuário ou senha incorretos"
                except Exception:
                    msg = "Usuário ou senha incorretos"
                return JSONResponse(status_code=401, content={"error": msg})

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return JSONResponse(status_code=401, content={"error": "Token não retornado pela API"})

            # 2. Decodificar JWT para extrair claims (sem verificação de assinatura)
            import base64
            try:
                parts = access_token.split(".")
                payload = parts[1]
                # Adicionar padding
                payload += "=" * (4 - len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
            except Exception as e:
                logger.error(f"[AUTH] Erro ao decodificar JWT: {e}")
                return JSONResponse(status_code=500, content={"error": "Erro ao processar token"})

            logger.info(f"[AUTH] JWT role={claims.get('role')} name={claims.get('name')} email={claims.get('email')}")

            # 3. Verificar se é admin
            role = claims.get("role", "")
            is_admin = isinstance(role, str) and role.lower() == "admin"

            if not is_admin:
                return JSONResponse(status_code=403, content={
                    "error": f"Acesso negado. Seu perfil ({role or 'sem role'}) não tem permissão de administrador."
                })

            # 4. Montar user_info a partir do JWT + token_data
            user_info = {
                "id": claims.get("nameid", ""),
                "name": claims.get("name", ""),
                "email": claims.get("email", ""),
                "userName": token_data.get("userName", claims.get("unique_name", "")),
                "role": role,
                "roles": [role] if role else [],
                "country": claims.get("country", ""),
                "state": claims.get("state", ""),
                "city": claims.get("city", ""),
                "vip": claims.get("vip", "false").lower() == "true" if isinstance(claims.get("vip"), str) else bool(claims.get("vip")),
            }

            return {"status": "ok", "access_token": access_token, "user": user_info}

    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Não foi possível conectar à API RemateWeb"})
    except Exception as e:
        logger.error(f"[AUTH] Erro no login: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Erro interno ao autenticar"})


@app.get("/api/auth/check")
async def auth_check(request: Request):
    """Verifica se o token JWT é válido e se o usuário é admin."""
    import base64
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Token não fornecido"})

    token = auth_header.replace("Bearer ", "")
    try:
        parts = token.split(".")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))

        # Verificar expiração
        import time as _time
        if claims.get("exp", 0) < _time.time():
            return JSONResponse(status_code=401, content={"error": "Token expirado"})

        # Verificar role admin
        role = claims.get("role", "")
        if not (isinstance(role, str) and role.lower() == "admin"):
            return JSONResponse(status_code=403, content={"error": "Acesso negado"})

        return {"status": "ok", "user": {
            "id": claims.get("nameid", ""),
            "name": claims.get("name", ""),
            "email": claims.get("email", ""),
            "role": role,
        }}
    except Exception as e:
        logger.error(f"[AUTH] Erro ao verificar token: {e}")
        return JSONResponse(status_code=401, content={"error": "Token inválido"})


# ============================================================
# Routes - API
# ============================================================
@app.post("/api/rooms")
async def create_room(req: CreateRoomRequest):
    room_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO rooms (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (room_id, req.name, now, now)
        )
        await db.commit()

    # Criar pasta de frames da sala
    os.makedirs(os.path.join("frames", room_id), exist_ok=True)

    return {"id": room_id, "name": req.name}


@app.get("/api/rooms")
async def list_rooms():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM rooms ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        rooms = []
        for row in rows:
            r = dict(row)
            r["is_active"] = r["id"] in active_streams and active_streams[r["id"]].running
            rooms.append(r)
        return rooms


@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Sala não encontrada")

        room = dict(row)

        # Get regions
        cursor = await db.execute("SELECT * FROM regions WHERE room_id = ?", (room_id,))
        regions = [dict(r) for r in await cursor.fetchall()]
        room["regions"] = regions

        # Stream status
        if room_id in active_streams:
            room["stream_status"] = active_streams[room_id].get_status()
        else:
            room["stream_status"] = None

        room["is_active"] = room_id in ocr_tasks and not ocr_tasks[room_id].done()

        return room


@app.post("/api/rooms/{room_id}/stream")
async def set_stream(room_id: str, req: SetStreamRequest):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM rooms WHERE id = ?", (room_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Sala não encontrada")

        await db.execute(
            "UPDATE rooms SET stream_url = ?, updated_at = ? WHERE id = ?",
            (req.stream_url, datetime.now().isoformat(), room_id)
        )
        await db.commit()

    # Stop existing stream if any
    if room_id in active_streams:
        active_streams[room_id].stop()

    # Create new stream manager
    sm = StreamManager(room_id, req.stream_url)
    active_streams[room_id] = sm

    # Resolve URL
    resolved = sm.resolve_url()
    if not resolved:
        return {"status": "error", "error": sm.error}

    # Capture first frame
    frame = sm.capture_single_frame()
    if frame:
        return {
            "status": "ok",
            "stream_type": sm.stream_type,
            "has_frame": True
        }
    else:
        return {
            "status": "error",
            "error": sm.error,
            "stream_type": sm.stream_type
        }


@app.get("/api/rooms/{room_id}/frame")
async def get_frame(room_id: str):
    """Retorna o frame atual como JPEG."""
    if room_id not in active_streams:
        raise HTTPException(status_code=404, detail="Stream não iniciado")

    sm = active_streams[room_id]
    frame = sm.get_current_frame()

    if not frame:
        # Tenta capturar um
        frame = sm.capture_single_frame()

    if not frame:
        raise HTTPException(status_code=404, detail="Nenhum frame disponível")

    return Response(content=frame, media_type="image/jpeg")


@app.get("/api/rooms/{room_id}/debug")
async def list_debug(room_id: str):
    """Lista imagens de debug disponíveis."""
    debug_dir = os.path.join("frames", room_id, "debug")
    if not os.path.exists(debug_dir):
        return {"files": []}
    files = [f for f in os.listdir(debug_dir) if f.endswith(".png")]
    files.sort()
    return {"files": files}


@app.get("/api/rooms/{room_id}/debug/{filename}")
async def get_debug_image(room_id: str, filename: str):
    """Serve uma imagem de debug."""
    filepath = os.path.join("frames", room_id, "debug", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Imagem não encontrada")
    return FileResponse(filepath, media_type="image/png")


@app.get("/api/rooms/{room_id}/stream.mjpeg")
async def mjpeg_stream(room_id: str):
    """Stream MJPEG ao vivo — use como src de <img> para ver ao vivo."""
    if room_id not in active_streams:
        raise HTTPException(status_code=404, detail="Stream não iniciado")

    from starlette.responses import StreamingResponse

    async def generate():
        boundary = b"--frame\r\n"
        while True:
            sm = active_streams.get(room_id)
            if not sm:
                break

            frame = sm.get_current_frame()
            if frame:
                yield (
                    boundary
                    + b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode()
                    + frame
                    + b"\r\n"
                )

            await asyncio.sleep(0.1)  # ~10 fps

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/rooms/{room_id}/latest")
async def get_latest(room_id: str):
    """Retorna o último resultado OCR salvo em memória."""
    result = last_results.get(room_id)
    if not result:
        return {"data": None}
    return {"data": result}


@app.get("/api/rooms/{room_id}/test-ocr")
async def test_ocr(room_id: str):
    """Endpoint de debug: roda OCR uma vez no frame atual e retorna resultado."""
    if room_id not in active_streams:
        return {"error": "Stream não iniciado"}

    sm = active_streams[room_id]
    frame = sm.get_current_frame()
    if not frame:
        frame = sm.capture_single_frame()
    if not frame:
        return {"error": "Sem frame", "stream_error": sm.error}

    # Pegar regiões do DB
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM regions WHERE room_id = ?", (room_id,))
        regions = [dict(r) for r in await cursor.fetchall()]

    if not regions:
        return {"error": "Sem regiões definidas"}

    # Rodar OCR
    try:
        result = await asyncio.to_thread(extract_from_bytes, frame, regions, room_id)
        return {"status": "ok", "result": result, "frame_size": len(frame), "regions": len(regions)}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/rooms/{room_id}/regions")
async def set_regions(room_id: str, req: SetRegionsRequest):
    async with aiosqlite.connect(DB_PATH) as db:
        # Delete existing regions
        await db.execute("DELETE FROM regions WHERE room_id = ?", (room_id,))

        # Insert new regions
        for region in req.regions:
            region_id = str(uuid.uuid4())[:8]
            await db.execute(
                "INSERT INTO regions (id, room_id, type, label, value, x, y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    region_id, room_id,
                    region.get("type", "custom"),
                    region.get("label", ""),
                    region.get("value", region.get("type", "custom")),
                    region["x"], region["y"],
                    region["width"], region["height"]
                )
            )

        await db.execute(
            "UPDATE rooms SET updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), room_id)
        )
        await db.commit()

    # Limpar debug crops antigos para evitar arquivos de regioes removidas
    debug_dir = os.path.join("frames", room_id, "debug")
    if os.path.exists(debug_dir):
        import shutil
        shutil.rmtree(debug_dir, ignore_errors=True)

    return {"status": "ok", "count": len(req.regions)}


# ============================================================
# Region Templates
# ============================================================
class CreateTemplateRequest(BaseModel):
    name: str
    regions: list[dict]


class ApplyTemplateRequest(BaseModel):
    template_id: str


@app.get("/api/templates")
async def list_templates():
    """Lista todos os templates de regiões."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM region_templates ORDER BY created_at DESC")
        templates = []
        for row in await cursor.fetchall():
            t = dict(row)
            t["regions"] = json.loads(t["regions"])
            templates.append(t)
    return templates


@app.post("/api/templates")
async def create_template(req: CreateTemplateRequest):
    """Cria um novo template de regiões."""
    template_id = uuid.uuid4().hex[:8]
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO region_templates (id, name, regions, created_at) VALUES (?, ?, ?, ?)",
            (template_id, req.name, json.dumps(req.regions, ensure_ascii=False), now)
        )
        await db.commit()
    return {"status": "ok", "id": template_id, "name": req.name}


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: str):
    """Remove um template."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM region_templates WHERE id = ?", (template_id,))
        await db.commit()
    return {"status": "ok"}


@app.post("/api/rooms/{room_id}/apply-template")
async def apply_template(room_id: str, req: ApplyTemplateRequest):
    """Aplica um template de regiões à sala."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Buscar template
        cursor = await db.execute("SELECT * FROM region_templates WHERE id = ?", (req.template_id,))
        template = await cursor.fetchone()
        if not template:
            raise HTTPException(status_code=404, detail="Template não encontrado")

        regions = json.loads(template["regions"])

        # Deletar regiões existentes da sala
        await db.execute("DELETE FROM regions WHERE room_id = ?", (room_id,))

        # Inserir regiões do template
        for region in regions:
            region_id = uuid.uuid4().hex[:8]
            await db.execute(
                "INSERT INTO regions (id, room_id, type, label, value, x, y, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (region_id, room_id, region["type"], region.get("label", ""),
                 region.get("value", ""), region["x"], region["y"],
                 region["width"], region["height"])
            )

        await db.execute(
            "UPDATE rooms SET updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), room_id)
        )
        await db.commit()

    return {"status": "ok", "count": len(regions), "template_name": template["name"]}


class UpdateAuctionIdRequest(BaseModel):
    auction_id: str


@app.post("/api/rooms/{room_id}/auction-id")
async def set_auction_id(room_id: str, req: UpdateAuctionIdRequest):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE rooms SET auction_id = ?, updated_at = ? WHERE id = ?",
            (req.auction_id, datetime.now().isoformat(), room_id)
        )
        await db.commit()
    return {"status": "ok", "auction_id": req.auction_id}


@app.post("/api/rooms/{room_id}/interval")
async def set_interval(room_id: str, req: UpdateIntervalRequest):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE rooms SET ocr_interval = ?, updated_at = ? WHERE id = ?",
            (req.interval, datetime.now().isoformat(), room_id)
        )
        await db.commit()
    return {"status": "ok", "interval": req.interval}


@app.post("/api/rooms/{room_id}/start")
async def start_extraction(room_id: str):
    """Inicia a extração OCR contínua."""
    # Get room info
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
        room = await cursor.fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="Sala não encontrada")
        room = dict(room)

        cursor = await db.execute("SELECT * FROM regions WHERE room_id = ?", (room_id,))
        regions = [dict(r) for r in await cursor.fetchall()]

    if not regions:
        raise HTTPException(status_code=400, detail="Nenhuma região definida")

    if room_id not in active_streams:
        if not room.get("stream_url"):
            raise HTTPException(status_code=400, detail="Stream não configurado")
        sm = StreamManager(room_id, room["stream_url"])
        sm.resolve_url()
        active_streams[room_id] = sm

    sm = active_streams[room_id]

    # Start stream capture
    interval = room.get("ocr_interval", 1.0)
    sm.start(interval=interval)

    # Cancel existing OCR task
    if room_id in ocr_tasks and not ocr_tasks[room_id].done():
        ocr_tasks[room_id].cancel()

    # Start OCR task
    task = asyncio.create_task(ocr_loop(room_id, regions, interval))
    ocr_tasks[room_id] = task

    # Update status
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE rooms SET status = 'running', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), room_id)
        )
        await db.commit()

    return {"status": "running", "interval": interval, "regions": len(regions)}


@app.post("/api/rooms/{room_id}/stop")
async def stop_extraction(room_id: str):
    """Para a extração OCR."""
    if room_id in ocr_tasks:
        ocr_tasks[room_id].cancel()
        del ocr_tasks[room_id]

    if room_id in active_streams:
        active_streams[room_id].stop()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE rooms SET status = 'idle', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), room_id)
        )
        await db.commit()

    return {"status": "stopped"}


@app.delete("/api/rooms/{room_id}")
async def delete_room(room_id: str):
    import shutil
    # Stop everything
    if room_id in ocr_tasks:
        ocr_tasks[room_id].cancel()
        del ocr_tasks[room_id]
    if room_id in active_streams:
        active_streams[room_id].stop()
        del active_streams[room_id]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM regions WHERE room_id = ?", (room_id,))
        await db.execute("DELETE FROM extractions WHERE room_id = ?", (room_id,))
        await db.execute("DELETE FROM lot_history WHERE room_id = ?", (room_id,))
        await db.execute("DELETE FROM bid_history WHERE room_id = ?", (room_id,))
        await db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        await db.commit()

    # Remover pasta de frames da sala
    room_frames = os.path.join("frames", room_id)
    if os.path.exists(room_frames):
        shutil.rmtree(room_frames)

    return {"status": "deleted"}


# ============================================================
# System Monitoring
# ============================================================
import psutil
import platform

_process = psutil.Process(os.getpid())
_boot_time = time.time()


@app.get("/api/system/stats")
async def system_stats():
    """Retorna métricas do sistema: CPU, RAM, disco, rede e processo."""
    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=0.3)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()

        # Memory
        mem = psutil.virtual_memory()

        # Disk
        disk = psutil.disk_usage("/")

        # Network
        net = psutil.net_io_counters()

        # Process info (this app)
        proc_mem = _process.memory_info()
        try:
            proc_cpu = _process.cpu_percent(interval=0)
        except Exception:
            proc_cpu = 0

        # Uptime
        uptime_seconds = int(time.time() - _boot_time)

        # Active rooms/tasks
        active_rooms = len([rid for rid, task in ocr_tasks.items() if not task.done()])
        active_connections = sum(len(conns) for conns in ws_connections.values())

        return {
            "cpu": {
                "percent": cpu_percent,
                "cores": cpu_count,
                "freq_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
            },
            "memory": {
                "total": mem.total,
                "used": mem.used,
                "available": mem.available,
                "percent": mem.percent,
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent,
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
            },
            "process": {
                "memory_rss": proc_mem.rss,
                "cpu_percent": proc_cpu,
                "pid": _process.pid,
            },
            "app": {
                "uptime_seconds": uptime_seconds,
                "active_ocr_rooms": active_rooms,
                "active_ws_connections": active_connections,
                "total_streams": len(active_streams),
            },
            "system": {
                "os": platform.system(),
                "os_version": platform.release(),
                "hostname": platform.node(),
                "python_version": platform.python_version(),
            }
        }
    except Exception as e:
        logger.error(f"[SYSTEM] Erro ao obter stats: {e}")
        return {"error": str(e)}


@app.get("/api/rooms/{room_id}/extractions")
async def get_extractions(room_id: str, limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM extractions WHERE room_id = ? ORDER BY extracted_at DESC LIMIT ?",
            (room_id, limit)
        )
        rows = await cursor.fetchall()
        return [{"id": r["id"], "data": json.loads(r["data"]), "extracted_at": r["extracted_at"]} for r in rows]


# ============================================================
# Lot Report
# ============================================================
class LotReportRequest(BaseModel):
    lot_number: str
    started_at: str
    ended_at: str
    final_value: Optional[str] = None
    bid_count: int = 0
    extra_data: Optional[dict] = None
    bids: list[dict] = []


@app.post("/api/rooms/{room_id}/lot-report")
async def save_lot_report(room_id: str, req: LotReportRequest):
    """Salva o relatório de um lote finalizado com todos os lances."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Inserir lote
        await db.execute(
            "INSERT INTO lot_history (room_id, lot_number, started_at, ended_at, final_value, bid_count, extra_data) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (room_id, req.lot_number, req.started_at, req.ended_at,
             req.final_value, req.bid_count,
             json.dumps(req.extra_data, ensure_ascii=False) if req.extra_data else None)
        )
        # Inserir lances
        for bid in req.bids:
            await db.execute(
                "INSERT INTO bid_history (room_id, lot_number, value, payload, captured_at) VALUES (?, ?, ?, ?, ?)",
                (room_id, req.lot_number, bid.get("value", ""),
                 json.dumps(bid.get("payload", {}), ensure_ascii=False),
                 bid.get("captured_at", datetime.now().isoformat()))
            )
        await db.commit()

    # Salvar no post_log
    async with aiosqlite.connect(DB_PATH) as db:
        # Buscar o novo lote atual (o que veio depois)
        new_lot = req.extra_data.get('_lote_atual', '?') if req.extra_data else '?'
        await db.execute(
            "INSERT INTO post_log (room_id, old_lot, new_lot, bid_count, timestamp) VALUES (?, ?, ?, ?, ?)",
            (room_id, req.lot_number, new_lot, req.bid_count, req.ended_at or datetime.now().isoformat())
        )
        await db.commit()

    return {"status": "ok", "lot": req.lot_number, "bids_saved": len(req.bids)}


@app.get("/api/rooms/{room_id}/lot-report")
async def get_lot_report(room_id: str):
    """Retorna o relatório com a versão mais recente de cada lote (sem duplicatas)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Buscar a versão mais recente de cada lote (maior id = mais atualizado)
        cursor = await db.execute(
            """SELECT lh.* FROM lot_history lh
               INNER JOIN (
                   SELECT lot_number, MAX(id) as max_id
                   FROM lot_history WHERE room_id = ?
                   GROUP BY lot_number
               ) latest ON lh.id = latest.max_id
               ORDER BY lh.id ASC""",
            (room_id,)
        )
        lots = []
        for r in await cursor.fetchall():
            lot = dict(r)
            # Limpar extra_data se existir
            if lot.get("extra_data"):
                try:
                    lot["extra_data"] = json.loads(lot["extra_data"])
                except Exception:
                    lot["extra_data"] = None
            lots.append(lot)

    return {"lots": lots, "total": len(lots)}


@app.delete("/api/rooms/{room_id}/lot-report")
async def clear_lot_report(room_id: str):
    """Limpa todo o relatório de lotes da sala."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM lot_history WHERE room_id = ?", (room_id,))
        await db.execute("DELETE FROM bid_history WHERE room_id = ?", (room_id,))
        await db.execute("DELETE FROM post_log WHERE room_id = ?", (room_id,))
        await db.commit()
    return {"status": "ok"}


@app.get("/api/rooms/{room_id}/post-log")
async def get_post_log(room_id: str):
    """Retorna o log de POSTs da sala."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM post_log WHERE room_id = ? ORDER BY id DESC LIMIT 50",
            (room_id,)
        )
        logs = [dict(r) for r in await cursor.fetchall()]
    return {"logs": logs}


# ============================================================
# WebSocket
# ============================================================
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()

    if room_id not in ws_connections:
        ws_connections[room_id] = []
    ws_connections[room_id].append(websocket)

    try:
        while True:
            # Keep connection alive, listen for messages
            data = await websocket.receive_text()
            # Client can send ping or commands
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_connections[room_id].remove(websocket)
        if not ws_connections[room_id]:
            del ws_connections[room_id]


async def broadcast_to_room(room_id: str, data: dict):
    """Envia dados para todos os WebSocket conectados à sala."""
    if room_id not in ws_connections:
        return
    disconnected = []
    for ws in ws_connections[room_id]:
        try:
            await ws.send_json(data)
        except:
            disconnected.append(ws)
    for ws in disconnected:
        ws_connections[room_id].remove(ws)


# ============================================================
# OCR Loop
# ============================================================
def _values_changed(old: dict, new: dict) -> bool:
    """Compara apenas os valores extraídos (sem 'raw') para detectar mudanças."""
    if not old:
        return True
    for key in new:
        if key == 'raw':
            continue
        if new.get(key) != old.get(key):
            return True
    return False


async def upsert_lot(room_id: str, lote: str, valor: str, now: str):
    """Insere ou atualiza o lote no banco. Se lote já existe, atualiza valor e lances."""
    # Filtrar ruído: ignorar lote "1" se já temos um lote multi-dígito recente
    last_lot = lot_state.get(room_id, {}).get('last_lot')
    if lote == '1' and last_lot and len(last_lot) > 1:
        return

    # Valor válido?
    val_clean = valor if valor and valor != '0' else None

    # Rastrear último valor visto POR LOTE (evita incrementar bid_count com o mesmo valor)
    lot_values = lot_state.setdefault(room_id, {}).setdefault('values', {})
    last_val_for_lot = lot_values.get(lote)

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Verificar se lote já existe no banco
            cursor = await db.execute(
                "SELECT id, final_value, bid_count FROM lot_history WHERE room_id = ? AND lot_number = ? ORDER BY id DESC LIMIT 1",
                (room_id, lote)
            )
            row = await cursor.fetchone()

            if row:
                # Lote já existe → atualizar se valor mudou
                row_id, db_value, db_bids = row
                if val_clean and val_clean != last_val_for_lot:
                    new_bids = (db_bids or 0) + 1
                    await db.execute(
                        "UPDATE lot_history SET final_value = ?, bid_count = ?, ended_at = ? WHERE id = ?",
                        (val_clean, new_bids, now, row_id)
                    )
                    lot_values[lote] = val_clean
                    logger.info(f"[LOT] Room {room_id}: Lote {lote} atualizado → {val_clean} ({new_bids} lances)")
                elif val_clean and not db_value:
                    # Tinha valor NULL, agora tem valor
                    await db.execute(
                        "UPDATE lot_history SET final_value = ?, bid_count = 1, ended_at = ? WHERE id = ?",
                        (val_clean, now, row_id)
                    )
                    lot_values[lote] = val_clean
            else:
                # Lote novo → inserir
                await db.execute(
                    "INSERT INTO lot_history (room_id, lot_number, started_at, ended_at, final_value, bid_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (room_id, lote, now, now, val_clean, 1 if val_clean else 0)
                )
                lot_values[lote] = val_clean
                logger.info(f"[LOT] Room {room_id}: Novo lote {lote} (valor: {val_clean})")

            await db.commit()

        # Atualizar último lote visto
        lot_state[room_id]['last_lot'] = lote

    except Exception as e:
        logger.error(f"[LOT] Room {room_id}: erro upsert: {e}")


async def ocr_loop(room_id: str, regions: list, interval: float):
    """Loop assíncrono que executa OCR periodicamente."""
    logger.info(f"[OCR] Starting loop for room {room_id} | {len(regions)} regions | interval={interval}s")

    while True:
        try:
            if room_id not in active_streams:
                logger.warning(f"[OCR] Room {room_id}: stream removed, stopping")
                break

            sm = active_streams[room_id]

            # Se o stream tem erro (offline), ignorar frame em cache
            if sm.error:
                frame = None
            else:
                frame = sm.get_current_frame()

            if not frame:
                # Contar frames sem sinal
                no_frame_count = getattr(ocr_loop, '_no_frame_count', {}).get(room_id, 0) + 1
                if not hasattr(ocr_loop, '_no_frame_count'):
                    ocr_loop._no_frame_count = {}
                ocr_loop._no_frame_count[room_id] = no_frame_count

                # Se sem frame por 10 segundos, alertar
                threshold = max(int(10 / interval), 5)
                if no_frame_count == threshold:
                    # Buscar nome da sala
                    room_name = room_id
                    try:
                        async with aiosqlite.connect(DB_PATH) as db:
                            cursor = await db.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
                            row = await cursor.fetchone()
                            if row: room_name = row[0]
                    except: pass
                    await send_telegram_alert(f"⚠️ Stream caiu!\n\n📺 Sala: <b>{room_name}</b>\n⏱ Sem sinal há ~30 segundos", room_id)

                    # Avisar o frontend via WebSocket
                    stream_error = sm.error or "Sem sinal"
                    friendly_msg = "Transmissão offline ou encerrada. Verifique se o link é de uma transmissão ao vivo."
                    if "Cannot get fragment" in stream_error or "Error opening" in stream_error:
                        friendly_msg = "A transmissão não está mais ao vivo. Verifique o link e tente novamente."
                    await broadcast_to_room(room_id, {
                        "type": "stream_error",
                        "message": friendly_msg,
                        "raw_error": stream_error
                    })

                logger.debug(f"[OCR] Room {room_id}: no frame ({no_frame_count}x), waiting...")
                await asyncio.sleep(interval)
                continue
            else:
                # Reset contador quando frame volta
                if hasattr(ocr_loop, '_no_frame_count') and room_id in ocr_loop._no_frame_count:
                    if ocr_loop._no_frame_count[room_id] > 0:
                        # Stream voltou! Notificar se tinha alertado
                        threshold = max(int(10 / interval), 5)
                        if ocr_loop._no_frame_count[room_id] >= threshold:
                            room_name = room_id
                            try:
                                async with aiosqlite.connect(DB_PATH) as db:
                                    cursor = await db.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
                                    row = await cursor.fetchone()
                                    if row: room_name = row[0]
                            except: pass
                            await send_telegram_alert(f"✅ Stream voltou!\n\n📺 Sala: <b>{room_name}</b>", room_id)
                    ocr_loop._no_frame_count[room_id] = 0

            # Run OCR in thread pool
            try:
                result = await asyncio.to_thread(extract_from_bytes, frame, regions, room_id)
            except Exception as ocr_err:
                logger.error(f"[OCR] Room {room_id}: OCR failed: {ocr_err}")
                await asyncio.sleep(interval)
                continue

            now = datetime.now().isoformat()
            prev = last_results.get(room_id)
            changed = _values_changed(prev, result)

            # Sempre envia via WebSocket (tempo real)
            await broadcast_to_room(room_id, {
                "type": "extraction",
                "data": result,
                "timestamp": now,
                "changed": changed
            })

            # Só salva no DB e no TXT quando o valor mudar
            if changed:
                logger.info(f"[OCR] Room {room_id}: CHANGED → {result}")
                last_results[room_id] = {k: v for k, v in result.items() if k != 'raw'}

                # Salvar data.txt na pasta da sala
                try:
                    data_path = os.path.join("frames", room_id, "data.txt")
                    conf = result.get("confidence", {})
                    with open(data_path, "w", encoding="utf-8") as f:
                        for key, value in result.items():
                            if key not in ('raw', 'confidence'):
                                c = conf.get(key, "")
                                if c != "":
                                    f.write(f"{key}={value} (conf: {c}%)\n")
                                else:
                                    f.write(f"{key}={value}\n")
                except Exception as txt_err:
                    logger.error(f"[OCR] Room {room_id}: TXT write error: {txt_err}")

                try:
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "INSERT INTO extractions (room_id, data, extracted_at) VALUES (?, ?, ?)",
                            (room_id, json.dumps(result, ensure_ascii=False), now)
                        )
                        await db.commit()
                except Exception as db_err:
                    logger.error(f"[OCR] Room {room_id}: DB write error: {db_err}")

            # ---- Rastrear lote e valor (UPSERT no banco) ----
            lote = result.get('lote', '0')
            valor = result.get('valor', '0')
            if lote and lote != '0':
                await upsert_lot(room_id, lote, valor, now)

            # ---- POST para API RemateWeb em tempo real ----
            if changed:
                try:
                    # Buscar auction_id da sala
                    async with aiosqlite.connect(DB_PATH) as db:
                        cursor = await db.execute("SELECT auction_id FROM rooms WHERE id = ?", (room_id,))
                        row = await cursor.fetchone()
                        auction_id = row[0] if row and row[0] else None
                    if auction_id:
                        await post_ocr_bid(room_id, int(auction_id), lote, valor)
                except Exception as api_err:
                    logger.error(f"[REMATEWEB] Room {room_id}: {api_err}")

            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info(f"[OCR] Room {room_id}: loop cancelled")
            break
        except Exception as e:
            logger.error(f"[OCR] Room {room_id}: error: {e}", exc_info=True)
            await asyncio.sleep(interval)

