from typing import TypedDict, List, Dict, Optional, Any

class BotState(TypedDict):
    session_id: str
    messages: List[Dict[str, str]]
    current_intent: Optional[str]
    target_version: Optional[str]
    is_version_ambiguous: bool
    retrieved_chunks: List[Dict[str, Any]]
    search_cycles: int
    needs_escalation: bool
    
    # Внутренние параметры состояния и флаги маршрутизации
    is_waiting_for_user: bool
    is_from_cache: bool
    topic_summary: Optional[str]
    original_query: Optional[str]
    rewritten_query: Optional[str]
    semantic_query: Optional[str]
    keywords: List[str]
    evaluator_verdict: Optional[str]
    evaluator_reasoning: Optional[str]
    final_answer: Optional[str]
    topic_change_pending_query: Optional[str]
    confidence: Optional[int]
    rejected_cache_ids: List[str]
    last_served_cache_id: Optional[str]
    last_served_candidate_id: Optional[str]
