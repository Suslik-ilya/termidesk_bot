from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
import json
import uuid
from app.services.redis_client import RedisStateClient
from app.agent.graph import bot_graph

websocket_router = APIRouter()
redis_client = RedisStateClient()

@websocket_router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    
    # Генерация уникального идентификатора сессии для WebSocket-подключения
    session_id = str(uuid.uuid4())
    logger.info(f"WebSocket client connected. Session ID: {session_id}")
    
    try:
        while True:
            # 1. Чтение входящего пакета данных
            data = await websocket.receive_text()
            payload = json.loads(data)
            user_text = payload.get("message", "")
            
            if not user_text:
                continue

            
            # 2. Инициализация и вызов LangGraph-ядра
            state = redis_client.get_state(session_id)
            state["messages"].append({"role": "user", "content": user_text})
            state["original_query"] = user_text
            
            try:
                new_state = await bot_graph.ainvoke(state)
            except Exception as graph_err:
                logger.error(f"Graph execution failed: {graph_err}")
                new_state = state
                new_state["final_answer"] = "⚙️ Произошла техническая ошибка нейросети при формулировании ответа. Пожалуйста, попробуйте еще раз."
            
            answer = new_state.get("final_answer", "Системная ошибка.")
            
            # 3. Сохранение истории диалога
            new_state["messages"].append({"role": "assistant", "content": answer})
            redis_client.save_state(session_id, new_state)
            
            message_id = new_state.get("last_served_cache_id", "new_gen")
            
            # 4. Отправка ответа клиенту
            # Передача JSON-пакета для последующего рендеринга на стороне фронтенда
            await websocket.send_text(json.dumps({
                "type": "message",
                "message_id": message_id,
                "content": answer,
                "confidence": new_state.get("confidence", 0),
                "is_from_cache": new_state.get("is_from_cache", False)
            }))
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected for Session {session_id}")
    except Exception as e:
        logger.error(f"WebSocket critical error: {e}")
        await websocket.close()
