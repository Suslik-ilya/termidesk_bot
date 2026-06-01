from app.agent.state import BotState
from loguru import logger

async def slot_filling_node(state: BotState) -> dict:
    logger.info(f"[Узел 2] Ожидание уточнений для сессии {state['session_id']}")
    
    question = "Для точного ответа мне нужно знать версию вашего Termidesk. Какая у вас установлена (например, 5.1, 6.0.2 или 6.1)?"
    
    logger.bind(state=state).debug("[Slot Filling] Состояние перед переходом")
    return {
        "is_waiting_for_user": True,
        "final_answer": question,
        "confidence": 10
    }
