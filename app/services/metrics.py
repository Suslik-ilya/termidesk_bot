from prometheus_client import Counter, Histogram, Summary

# Общее количество запросов к боту
bot_requests_total = Counter(
    'bot_requests_total',
    'Общее количество запросов к боту',
    ['intent', 'target_version']
)

# Количество эскалаций на оператора
bot_escalations_total = Counter(
    'bot_escalations_total',
    'Общее количество эскалаций на оператора'
)

# Количество циклов поиска (попыток перефразирования/уточнения)
bot_search_cycles = Histogram(
    'bot_search_cycles',
    'Количество циклов поиска на запрос',
    buckets=[1, 2, 3, 4, 5, float('inf')]
)

# Количество попаданий в кэш
bot_cache_hits_total = Counter(
    'bot_cache_hits_total',
    'Общее количество попаданий в кэш'
)

# Задержки LLM
llm_latency_seconds = Histogram(
    'llm_latency_seconds',
    'Задержка запросов к LLM в секундах',
    ['model', 'node_name'],
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, float('inf')]
)
