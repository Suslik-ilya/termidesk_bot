import asyncio
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from loguru import logger

from app.adapters.telegram_formatter import format_and_split

from config.settings import settings
from app.services.redis_client import RedisStateClient
from app.agent.graph import bot_graph

router = Router()
redis_client = RedisStateClient()


from app.services.cache_service import CacheService

def get_feedback_keyboard(session_id: str, item_id: str):
    if not item_id:
        return None
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="👍 Помогло", callback_data=f"fb_1_{session_id}_{item_id}"))
    builder.add(InlineKeyboardButton(text="👎 Не помогло", callback_data=f"fb_0_{session_id}_{item_id}"))
    return builder.as_markup()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Здравствуйте! Я умный ИИ-помощник технической поддержки Termidesk. Опишите вашу проблему или задайте вопрос.")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    text = "Я могу помочь с настройкой и устранением ошибок в Termidesk VDI. Просто опишите свою проблему свободным языком.\n\nКоманды:\n/ticket — создать заявку инженеру\n/reset — начать новый диалог (очистить контекст)"
    await message.answer(text)

@router.message(Command("reset"))
async def cmd_reset(message: types.Message):
    """Принудительный сброс контекста диалога"""
    session_id = str(message.from_user.id)
    redis_client.clear_state(session_id)
    await message.answer("🔄 Контекст диалога сброшен. Я забыл всё, о чем мы говорили ранее. Можете задавать новый вопрос!")

@router.message(Command("ticket"))
async def cmd_ticket(message: types.Message):
    """Явная эскалация через команду"""
    session_id = str(message.from_user.id)
    state = redis_client.get_state(session_id)
    state["current_intent"] = "escalation"
    state["messages"].append({"role": "user", "content": "Принудительная заявка /ticket"})
    
    new_state = await bot_graph.ainvoke(state)
    redis_client.save_state(session_id, new_state)
    
    answer = new_state.get("final_answer", "Заявка передана.")
    await message.answer(answer)


@router.message()
async def process_user_message(message: types.Message):
    session_id = str(message.from_user.id)
    user_text = message.text

    # Инициализация индикатора набора текста
    async def keep_typing():
        while True:
            try:
                await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    typing_task = asyncio.create_task(keep_typing())

    state = redis_client.get_state(session_id)
    state["messages"].append({"role": "user", "content": user_text})
    state["original_query"] = user_text

    try:
        new_state = await bot_graph.ainvoke(state)
        answer = new_state.get("final_answer", "Бот не смог сформировать ответ.")
    except Exception as e:
        logger.error(f"Ошибка графа: {e}")
        answer = "⚙️ Произошла техническая ошибка нейросети. Пожалуйста, попробуйте позже."
        new_state = state

    finally:
        typing_task.cancel()
        
    new_state["messages"].append({"role": "assistant", "content": answer})
    redis_client.save_state(session_id, new_state)

    # --- Логика отображения клавиатуры обратной связи ---
    current_intent = new_state.get("current_intent", "info")
    # Скрытие элементов управления при ожидании ответа, эскалации или обработке приветствия/оффтопа
    hide_buttons = (
            new_state.get("is_waiting_for_user") or
            new_state.get("needs_escalation") or
            current_intent in ["greeting", "off_topic", "escalation"]
    )

    item_id = new_state.get("last_served_cache_id")
    if not item_id:
        item_id = "new_gen"

    keyboard = None
    if not hide_buttons:
        keyboard = get_feedback_keyboard(session_id, item_id)

    # Конвертируем Markdown → Telegram HTML и разбиваем на части при необходимости
    message_parts = format_and_split(answer)
    for i, part in enumerate(message_parts):
        # Добавление inline-клавиатуры только к последнему фрагменту сообщения
        part_keyboard = keyboard if i == len(message_parts) - 1 else None
        try:
            await message.answer(part, parse_mode="HTML", reply_markup=part_keyboard)
        except Exception as e:
            logger.warning(f"Ошибка отправки HTML-сообщения: {e}. Отправляем без форматирования.")
            await message.answer(part, reply_markup=part_keyboard)

@router.callback_query(lambda c: c.data and c.data.startswith('fb_'))
async def process_feedback(callback_query: types.CallbackQuery):
    """Обработка кнопок [👍 Помогло] и [👎 Не помогло]"""
    parts = callback_query.data.split('_')
    if len(parts) >= 4:
        # fb_1_sessionid_itemid
        action = parts[1]
        session_id = parts[2]
        item_id = "_".join(parts[3:])
        
        cache_service = CacheService()
        delta = 1 if action == "1" else -1
        
        if item_id == "new_gen":
            if delta == 1:
                # Сохранение кандидата при наличии положительной оценки и статусе проверки "yes" или "partial"
                state = redis_client.get_state(session_id)
                if state.get("evaluator_verdict") in ["yes", "partial"]:
                    query = state.get("original_query", "")
                    ans = state.get("final_answer", "")
                    target = state.get("target_version")
                    await cache_service.save_candidate(query, ans, target)
        else:
            cache_service.update_rating(item_id, delta)
            
            if delta == -1:
                state = redis_client.get_state(session_id)
                rejected = state.get("rejected_cache_ids", [])
                if item_id not in rejected:
                    rejected.append(item_id)
                    state["rejected_cache_ids"] = rejected
                    redis_client.save_state(session_id, state)
            
    logger.info(f"Telegram обратная связь: {callback_query.data}")
    await callback_query.answer(f"Спасибо! Ваш отзыв учтен.", show_alert=False)
    
    # Удаление inline-клавиатуры после получения оценки
    await callback_query.message.edit_reply_markup(reply_markup=None)

async def main():
    if not settings.telegram_bot_token:
        logger.warning("Токен Telegram отсутствует.")
        return
        
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Запуск aiogram polling...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
