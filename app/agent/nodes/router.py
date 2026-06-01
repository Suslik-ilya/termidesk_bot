from app.agent.state import BotState
from app.services.llm_client import LLMClient
from loguru import logger
from app.services.metrics import bot_requests_total

ROUTER_SCHEMA = {
  "type": "object",
  "properties": {
    "intent": { "type": "string", "enum": ["info", "troubleshooting", "escalation", "comparison", "greeting", "off_topic"] },
    "version": { "type": ["string", "null"] },
    "is_version_ambiguous": { "type": "boolean" },
    "is_new_topic": { "type": "boolean", "description": "True если это начало новой темы, False если это уточнение или продолжение прошлой темы" },
    "topic_summary": { "type": "string", "description": "Суть обращения в 3-5 словах" }
  },
  "required": ["intent", "version", "is_version_ambiguous", "is_new_topic", "topic_summary"]
}

ROUTER_PROMPT = """Ты — интеллектуальный маршрутизатор (Router) системы технической поддержки Termidesk VDI.
Твоя задача — проанализировать сообщение пользователя и извлечь метаданные для дальнейшей обработки.

Правила определения Intent:
- "info": вопросы по документации, настройке, характеристикам продукта.
- "troubleshooting": сообщения об ошибках, сбоях, просьбы помочь починить.
- "escalation": ЖЕЛЕЗНО ставь этот интент, если пользователь явно просит позвать человека, оператора, создать заявку, обращается в поддержку (например: "хочу обратиться в поддержку", "оператор", "заявка", "создай тикет"). Также сюда относится ответ "да" на предложение бота создать заявку.
- "comparison": просьбы сравнить версии или продукты.
- "greeting": ЖЕЛЕЗНО ставь этот интент для приветствий, базовых вопросов о боте и благодарностей (например: "привет", "кто ты", "кто ты такой", "что ты умеешь", "спасибо").
- "off_topic": вопросы, вообще не связанные с Termidesk, VDI, Linux или IT.

Правила определения Version:
Найди упоминание версии продукта (например, 5.1, 6.0, 6.1). Если версия не указана явно, верни null.
Если в истории диалога пользователь уже указывал версию — используй её. Не ставь is_version_ambiguous=True, если версия уже известна из контекста диалога.

Установи is_version_ambiguous = true ТОЛЬКО если:
1. Intent равен "info" или "troubleshooting"
2. И version равна null
3. И версия НЕ была указана ранее в истории диалога
4. И вопрос ТРЕБУЕТ знания конкретной версии для точного ответа

Для общих вопросов о продукте Termidesk (что это такое, кто разработчик, для чего нужен, какие функции, общие возможности) — используй intent="info" и is_version_ambiguous=false. Такие вопросы НЕ требуют знания версии.

Правила определения is_new_topic:
- Установи true, если текущее сообщение пользователя начинает новую тему, задает новый вопрос, не связанный с предыдущим диалогом.
- Установи false, если пользователь отвечает на уточняющий вопрос (например, просто называет версию "6.1.1", или говорит "да"), или если он продолжает ту же самую проблему (задает уточняющие вопросы по тому же тикету)."""

async def router_node(state: BotState) -> dict:
    logger.info(f"[Узел 1] Маршрутизатор запущен для сессии {state['session_id']}")
    llm = LLMClient()

    last_user_msg = ""
    for msg in reversed(state.get("messages", [])):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    # --- Блок 1: Обработка подтверждения процесса эскалации ---
    # Ожидание бинарного ответа (да/нет) после предложения создания заявки
    if state.get("current_intent") == "escalation_proposal":
        user_answer = last_user_msg.lower().strip()
        if any(word in user_answer for word in ["да", "давай", "создай", "хочу", "конечно", "ок"]):
            logger.info("[Router] Пользователь подтвердил эскалацию. Создаём заявку.")
            return {
                "current_intent": "escalation",
                "original_query": state.get("original_query", last_user_msg),
                "is_waiting_for_user": False,
                "topic_change_pending_query": None,
            }
        else:
            logger.info("[Router] Пользователь отказался от эскалации.")
            return {
                "current_intent": "greeting",
                "is_waiting_for_user": False,
                "topic_change_pending_query": None,
                "final_answer": "Хорошо, заявка не создана. Если у Вас есть другие вопросы — я готов помочь!",
            }

    # --- Блок 2: Обработка ожидающего подтверждения смены темы ---
    pending_query = state.get("topic_change_pending_query")
    if pending_query:
        user_answer = last_user_msg.lower().strip()
        if "да" in user_answer or "нов" in user_answer:
            # Инициализация новой темы: сброс контекста диалога
            messages_to_keep = [{"role": "user", "content": pending_query}]
            last_user_msg = pending_query
            logger.info("[Router] Пользователь подтвердил новую тему. Очищаем историю.")
        elif "нет" in user_answer:
            # Отклонение смены темы: восстановление контекста и обработка отложенного запроса
            messages_to_keep = state.get("messages", [])
            if len(messages_to_keep) >= 3:
                messages_to_keep = messages_to_keep[:-2]
            last_user_msg = pending_query
            logger.info("[Router] Пользователь решил продолжить тему. Восстанавливаем контекст.")
        else:
            # Отсутствие прямого ответа на запрос смены темы: приоритетная обработка нового сообщения
            messages_to_keep = [{"role": "user", "content": last_user_msg}]
            logger.info("[Router] Пользователь написал новый запрос, игнорируя подтверждение темы.")

        # Инъекция известной версии продукта из состояния сессии
        known_version = state.get("target_version")
        version_hint = f"\nТекущая известная версия продукта: {known_version}" if known_version else "\nВерсия продукта пока не указана."

        # Формирование обновленного контекста для LLM
        recent_history = messages_to_keep[-5:]
        history_str = "\n".join([f"{m['role']}: {m['content']}" for m in recent_history])
        context = f"История:\n{history_str}\n{version_hint}\n\nТекущее сообщение: {last_user_msg}"
    else:
        messages_to_keep = state.get("messages", [])

        # Инъекция известной версии продукта из состояния сессии
        known_version = state.get("target_version")
        version_hint = f"\nТекущая известная версия продукта: {known_version}" if known_version else "\nВерсия продукта пока не указана."

        recent_history = messages_to_keep[-5:]
        history_str = "\n".join([f"{m['role']}: {m['content']}" for m in recent_history])
        context = f"История:\n{history_str}\n{version_hint}\n\nТекущее сообщение: {last_user_msg}"

    result = await llm.call_router(ROUTER_PROMPT, context, ROUTER_SCHEMA)
    logger.debug(f"[Router Node] Разбор: {result}")

    # --- Блок корректировки бизнес-логики ---
    # Принудительное отключение флага неоднозначности версии для информационных и нецелевых запросов
    if result["intent"] not in ["info", "troubleshooting"]:
        result["is_version_ambiguous"] = False

    # Исключение повторного запроса при наличии зафиксированной версии
    if known_version and result["version"] is None:
        result["version"] = known_version
        result["is_version_ambiguous"] = False

    intent = result["intent"]
    version = result["version"] if result["version"] else state.get("target_version")

    # Обновление Prometheus-метрик
    bot_requests_total.labels(intent=intent, target_version=version or "unknown").inc()

    # --- Быстрая обработка интента 'greeting' без активации Retrieval ---
    if intent == "greeting":
        logger.info("[Router] Обнаружено приветствие, отвечаем напрямую.")
        return {
            "messages": messages_to_keep,
            "current_intent": "greeting",
            "target_version": version,
            "is_version_ambiguous": False,
            "is_waiting_for_user": False,
            "topic_change_pending_query": None,
            "final_answer": "Здравствуйте! Я — ИИ-помощник технической поддержки Termidesk VDI. Могу помочь с настройкой, устранением ошибок и ответить на вопросы по документации. Опишите вашу проблему или задайте вопрос!",
        }

    # --- Быстрая обработка интента 'off_topic' ---
    if intent == "off_topic":
        logger.info("[Router] Обнаружен вопрос не по теме.")
        return {
            "messages": messages_to_keep,
            "current_intent": "off_topic",
            "target_version": version,
            "is_version_ambiguous": False,
            "is_waiting_for_user": False,
            "topic_change_pending_query": None,
            "final_answer": "Я специализируюсь на вопросах, связанных с Termidesk VDI. Пожалуйста, задайте вопрос по теме продукта, и я постараюсь помочь.",
        }

    # --- Инициация процесса эскалации с запросом подтверждения ---
    if intent == "escalation":
        logger.info("[Router] Обнаружена просьба об эскалации. Запрашиваем подтверждение.")
        return {
            "messages": messages_to_keep,
            "current_intent": "escalation_proposal",
            "target_version": version,
            "original_query": last_user_msg,
            "topic_summary": result.get("topic_summary", "Запрос на поддержку"),
            "is_waiting_for_user": True,
            "topic_change_pending_query": None,
            "final_answer": "Вы хотите, чтобы я создал заявку в службу поддержки и передал инженерам всю историю нашего диалога? (Напишите «Да» для подтверждения)",
        }

    # --- Обнаружение смены темы с запросом подтверждения ---
    if result.get("is_new_topic") and len(messages_to_keep) > 1:
        logger.info("[Router] Обнаружена новая тема! Запрашиваем подтверждение у пользователя.")
        return {
            "messages": messages_to_keep,
            "current_intent": "topic_change_confirmation",
            "is_waiting_for_user": True,
            "topic_change_pending_query": last_user_msg,
            "final_answer": "🔄 Похоже, вы задаете вопрос на новую тему. Хотите ли вы очистить историю предыдущего диалога, чтобы она не мешала поиску?\n*(Напишите «Да», чтобы начать заново, или «Нет», чтобы продолжить в текущем контексте)*"
        }

    # --- Стандартная маршрутизация для info / troubleshooting / comparison ---
    logger.bind(state=state).debug("[Router] Состояние перед переходом")
    return {
        "messages": messages_to_keep,
        "current_intent": intent,
        "target_version": version,
        "is_version_ambiguous": result["is_version_ambiguous"],
        "topic_summary": result["topic_summary"],
        "original_query": last_user_msg,
        "search_cycles": 0,
        "needs_escalation": False,
        "is_waiting_for_user": False,
        "topic_change_pending_query": None,
    }
