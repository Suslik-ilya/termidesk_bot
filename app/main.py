from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uuid
import os
import uvicorn
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator
from contextlib import asynccontextmanager
import asyncio
from fastapi import BackgroundTasks

from config.settings import settings
from app.services.redis_client import RedisStateClient
from app.services.log_config import setup_logging
from app.agent.graph import bot_graph
from app.adapters.web_socket import websocket_router
from app.adapters.telegram_bot import main as start_telegram_bot
from app.adapters.admin_panel import admin_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация логирования (stdout + Logstash TCP)
    setup_logging()
    # Запуск фонового процесса Telegram-бота при старте FastAPI
    logger.info("Запуск Telegram-бота...")
    tg_task = asyncio.create_task(start_telegram_bot())
    yield
    # Остановка бота при завершении работы сервера
    tg_task.cancel()
    try:
        await tg_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Termidesk Support Bot API", lifespan=lifespan)

redis_client = RedisStateClient()

class ChatRequest(BaseModel):
    session_id: str
    message: str

class FeedbackRequest(BaseModel):
    message_id: str
    session_id: str
    feedback: str # 'like' or 'dislike'

# --- 1. Prometheus Metrics ---
# Подключение сборщика DREDD/Prometheus метрик
Instrumentator().instrument(app).expose(app)

# --- 2. REST API эндпоинты ---
@app.post("/api/v1/chat")
async def chat_endpoint(request: ChatRequest):
    logger.info(f"API запрос чата получен для сессии: {request.session_id}")
    
    state = redis_client.get_state(request.session_id)
    state["messages"].append({"role": "user", "content": request.message})
    state["original_query"] = request.message
    
    new_state = await bot_graph.ainvoke(state)
    
    if new_state.get("final_answer"):
        new_state["messages"].append({"role": "assistant", "content": new_state["final_answer"]})
    
    redis_client.save_state(request.session_id, new_state)
    
    item_id = new_state.get("last_served_cache_id", "new_gen")
    
    return {
        "response": new_state.get("final_answer", "Внутренняя ошибка графа."),
        "confidence": new_state.get("confidence", 0),
        "is_from_cache": new_state.get("is_from_cache", False),
        "message_id": item_id
    }

@app.post("/api/v1/feedback")
async def feedback_endpoint(request: FeedbackRequest, background_tasks: BackgroundTasks):
    logger.info(f"Получен отзыв {request.feedback} для сообщения {request.message_id} (Сессия {request.session_id})")
    if request.feedback not in ["like", "dislike"]:
        raise HTTPException(status_code=400, detail="Неверный тип отзыва.")
    
    # Обновление рейтинга в кэше асинхронно
    from app.services.cache_service import CacheService
    
    cache_service = CacheService()
    delta = 1 if request.feedback == "like" else -1
    
    async def process_feedback():
        if request.message_id == "new_gen" or request.message_id == "":
            if delta == 1:
                state = redis_client.get_state(request.session_id)
                if state.get("evaluator_verdict") in ["yes", "partial"]:
                    query = state.get("original_query", "")
                    ans = state.get("final_answer", "")
                    target = state.get("target_version")
                    await cache_service.save_candidate(query, ans, target)
        else:
            cache_service.update_rating(request.message_id, delta)
            
            if delta == -1:
                state = redis_client.get_state(request.session_id)
                rejected = state.get("rejected_cache_ids", [])
                if request.message_id not in rejected:
                    rejected.append(request.message_id)
                    state["rejected_cache_ids"] = rejected
                    redis_client.save_state(request.session_id, state)

    background_tasks.add_task(process_feedback)
    
    return {"status": "ok", "message": "Отзыв успешно записан."}

class ResetRequest(BaseModel):
    session_id: str

@app.post("/api/v1/reset")
async def reset_endpoint(request: ResetRequest):
    logger.info(f"Получен запрос на сброс для сессии: {request.session_id}")
    redis_client.clear_state(request.session_id)
    return {"status": "ok", "message": "Контекст успешно сброшен."}

# --- 3. WebSocket роутер ---
app.include_router(websocket_router)

# --- Admin Panel Router ---
app.include_router(admin_router)

# --- 4. Статика (Web Chat Frontend) ---
# Настройка отдачи index.html при заходе в корень сервера
frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

@app.get("/")
async def get_index():
    index_file = os.path.join(frontend_path, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Фронтенд не найден. Убедитесь, что директория frontend существует."}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.fastapi_host, port=settings.fastapi_port, reload=True)
