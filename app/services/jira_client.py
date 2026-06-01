import json
import base64
import requests
from loguru import logger
from config.settings import settings

class JiraClient:
    def __init__(self):
        self.base_url = settings.jira_url
        self.user = settings.jira_user
        self.token = settings.jira_api_token
        self.project_key = settings.jira_project_key
        
        # Формирование заголовка Authorization (Basic Auth для Jira REST API)
        if self.user and self.token:
            auth_str = f"{self.user}:{self.token}"
            encoded_auth = base64.b64encode(auth_str.encode()).decode()
            self.auth_header = {"Authorization": f"Basic {encoded_auth}"}
        else:
            self.auth_header = {}

    def create_issue(self, summary: str, description: str, labels: list[str]) -> str:
        """
        Создание заявки (Task) в Jira.
        Возвращает идентификатор созданной заявки (например, 'TDSK-SUP-123') или None.
        """
        if not self.base_url:
            logger.warning("URL Jira не настроен. Создание задачи пропущено в режиме dry-run.")
            return "TDSK-SUP-DRYRUN"

        endpoint = f"{self.base_url}/rest/api/2/issue"
        
        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "issuetype": {"name": "Task"},
                "summary": summary,
                "description": description,
                "priority": {"name": "High"},
                "labels": labels
            }
        }
        
        headers = self.auth_header.copy()
        headers["Content-Type"] = "application/json"
        
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            data = response.json()
            issue_key = data.get("key")
            logger.info(f"Успешно создана задача в Jira: {issue_key}")
            return issue_key
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось создать задачу в Jira: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Ответ с ошибкой от Jira API: {e.response.text}")
            return None

    def attach_state_log(self, issue_key: str, state: dict, filename: str = "bot_reasoning.json"):
        """
        Прикрепление JSON-лога состояния (BotState) к задаче.
        Тип запроса: multipart/form-data.
        """
        if not self.base_url or not issue_key or issue_key == "TDSK-SUP-DRYRUN":
            return

        endpoint = f"{self.base_url}/rest/api/2/issue/{issue_key}/attachments"
        
        headers = self.auth_header.copy()
        # Обязательный заголовок для загрузки файлов через Jira REST API
        headers["X-Atlassian-Token"] = "no-check"
        
        state_json = json.dumps(state, indent=2, ensure_ascii=False)
        files = {
            "file": (filename, state_json, "application/json")
        }
        
        try:
            response = requests.post(endpoint, headers=headers, files=files, timeout=15)
            response.raise_for_status()
            logger.info(f"Успешно прикреплен лог рассуждений к {issue_key}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось прикрепить лог рассуждений к задаче Jira {issue_key}: {e}")
