import json
import asyncio
import time
import requests
import time
from loguru import logger
from config.settings import settings
from app.services.metrics import llm_latency_seconds


class LLMClient:
    def __init__(self):
        # Использование эндпоинта ProxyAPI для доступа к OpenAI
        self.base_url = "https://api.proxyapi.ru/openai/v1/chat/completions"
        self.api_key = settings.llm_api_key
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def _make_request(self, payload: dict) -> dict:
        def fetch():
            for attempt in range(3):
                try:
                    response = requests.post(self.base_url, headers=self.headers, json=payload, timeout=45)
                    response.raise_for_status()
                    return response.json()
                except requests.exceptions.RequestException as e:
                    if attempt == 2:
                        raise e
                    logger.warning(f"Сбой запроса к API (попытка {attempt+1}/3), повтор через 2с... Ошибка: {e}")
                    time.sleep(2)

        try:
            data = await asyncio.to_thread(fetch)
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Сбой запроса к API после 3 попыток: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Детали ошибки: {e.response.text}")
            raise

    async def structured_call(self, model: str, system_prompt: str, user_message: str, schema: dict, node_name: str = "unknown") -> dict:
        """Синхронный вызов LLM с валидацией JSON-ответа"""
        # Инъекция JSON-схемы в системный промпт для гарантии формата
        schema_json = json.dumps(schema, ensure_ascii=False)
        full_system = f"{system_prompt}\n\nОТВЕТЬ СТРОГО В ФОРМАТЕ JSON ПО СХЕМЕ:\n{schema_json}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        start_time = time.perf_counter()
        try:
            response = await self._make_request(payload)
            latency = time.perf_counter() - start_time
            llm_latency_seconds.labels(model=model, node_name=node_name).observe(latency)
        except Exception as e:
            latency = time.perf_counter() - start_time
            llm_latency_seconds.labels(model=model, node_name=node_name).observe(latency)
            raise

        try:
            content = response["choices"][0]["message"]["content"]
            result = json.loads(content)
            
            logger.bind(
                latency=latency,
                system_prompt=full_system,
                user_message=user_message,
                raw_response=content
            ).info(f"Вызов LLM к {model} завершен за {latency:.3f}с")
            
            return result
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"Ошибка парсинга JSON: {e}\nОтвет: {response}")
            raise

    async def call_router(self, system_prompt: str, user_message: str, schema: dict) -> dict:
        return await self.structured_call("gpt-4o-mini", system_prompt, user_message, schema, node_name="router")

    async def call_evaluator(self, system_prompt: str, user_message: str, schema: dict) -> dict:
        return await self.structured_call("gpt-4o", system_prompt, user_message, schema, node_name="evaluator")

    async def call_generator(self, system_prompt: str, user_message: str, schema: dict) -> dict:
        return await self.structured_call("gpt-4o", system_prompt, user_message, schema, node_name="generator")
