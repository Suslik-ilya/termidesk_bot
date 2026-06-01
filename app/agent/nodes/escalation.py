from app.agent.state import BotState
from app.services.jira_client import JiraClient
from loguru import logger
from app.services.metrics import bot_escalations_total


async def propose_escalation_node(state: BotState) -> dict:
    """Предложение эскалации запроса на уровень технической поддержки."""
    logger.info(f"[Узел 8a] Предложение эскалации для сессии {state['session_id']}")

    answer = (
        "К сожалению, мне не удалось найти точный ответ на ваш вопрос в документации. "
        "Хотите, чтобы я передал ваш вопрос нашим инженерам и создал заявку в системе поддержки?"
    )

    logger.bind(state=state).debug("[Propose Escalation] Состояние перед переходом")
    return {
        "final_answer": answer,
        "is_waiting_for_user": True,
        "needs_escalation": False
    }


async def escalation_node(state: BotState) -> dict:
    """Создание тикета в Jira при явном согласии пользователя."""
    logger.info(f"[Узел 8b] Эскалация в Jira для сессии {state['session_id']}")
    
    bot_escalations_total.inc()
    
    jira = JiraClient()

    topic = state.get("topic_summary", "Запрос на поддержку")
    version = state.get("target_version", "Не указана")
    query = state.get("original_query", "")

    summary = f"[AI Bot] {topic} - Версия: {version}"

    messages = state.get("messages", [])
    history_lines = []
    for m in messages[-6:]:  # Извлечение последних сообщений из истории диалога
        role = "Пользователь" if m.get("role") == "user" else "Бот"
        history_lines.append(f"**{role}**: {m.get('content', '')}")
    formatted_history = "\n\n".join(history_lines)

    search_cycles = state.get("search_cycles", 0)
    confidence = state.get("confidence", "N/A")

    description = (
        f"Инициатор: SESSION_{state['session_id']}\n\n"
        f"*Запрос пользователя:*\n{query}\n\n"
        f"*История диалога:*\n{formatted_history}\n\n"
        f"*Статус ИИ:*\nПопыток поиска: {search_cycles}. Уверенность: {confidence}/10."
    )

    labels = ["ai-bot-escalated"]
    if version and version != "Не указана":
        labels.append(f"v{version}")

    issue_key = jira.create_issue(summary, description, labels)

    # Обработка успешного создания тикета или тестового прогона
    if issue_key:
        if issue_key != "TDSK-SUP-DRYRUN":
            jira.attach_state_log(issue_key, state)
        answer = f"Я передал всю историю нашего диалога инженерам поддержки. Заявка создана (ID: {issue_key}). Ожидайте ответа!"
    else:
        answer = "Я пытался создать заявку инженерам, но произошла ошибка связи с сервером поддержки. Пожалуйста, попробуйте чуть позже."

    # Сброс флага эскалации для предотвращения зацикливания графа
    logger.bind(state=state).debug("[Escalation] Состояние перед переходом")
    return {"final_answer": answer, "needs_escalation": False}
