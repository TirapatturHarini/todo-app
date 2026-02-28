from prometheus_client import Counter, start_http_server
import time

# Expose HTTP metrics on port 9102
start_http_server(9102)

REQUESTS = Counter("myapp_requests_total", "Total requests")

while True:
    REQUESTS.inc()
    time.sleep(5)
