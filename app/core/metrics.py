"""Prometheus 指标：请求量 / 耗时（/metrics 由 main.py 挂载）。"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


def metrics_body() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
