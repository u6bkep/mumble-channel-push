import multiprocessing
import os

# Dynamic bind from environment (defaults match app expectations)
HOST = os.environ.get("CVP_HTTP_HOST", "0.0.0.0")
PORT = os.environ.get("CVP_HTTP_PORT", "5001")
bind = f"{HOST}:{PORT}"

# Workers: use gthread so callbacks (Ice + Flask) can coexist without forcing async
# Choose a modest thread count; SSE long-lived connections benefit from threads.
workers = int(os.environ.get("GUNICORN_WORKERS", str(max(1, multiprocessing.cpu_count() // 2))))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")

# Logging: let application configure levels; capture access separately if desired.
accesslog = "-"  # stdout
errorlog = "-"   # stdout
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", os.environ.get("MUMBLE_LOG_LEVEL", "info")).lower()

# Graceful timeouts (SSE may be long-lived; don't kill too fast)
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "120"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

# Recycle workers periodically to avoid resource accumulation
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "0"))  # 0 disables
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "0"))

# Security: limit forwarded headers if behind proxy (user can extend)
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "*")

# Hooks

def post_fork(server, worker):
    """Initialize ICE connection inside each worker after fork."""
    from channel_push import init_services
    import signal
    
    # Set up SIGUSR1 handler for debugging in each worker
    def worker_debug_handler(sig, frame):
        from channel_push import debug_handler
        server.log.info("Worker %s: SIGUSR1 received, dumping diagnostics", worker.pid)
        debug_handler(sig, frame)
    
    signal.signal(signal.SIGUSR1, worker_debug_handler)
    
    try:
        init_services()  # uses env
        server.log.info("Worker %s: Mumble ICE services initialized", worker.pid)
    except Exception as e:
        server.log.error("Worker %s: Failed to initialize services: %s", worker.pid, e)
        # If init fails, exit worker so Gunicorn can retry / fail-fast
        import sys
        sys.exit(1)

# When workers exit, atexit in init_services handles cleanup.


# To dump diagnostics from ALL workers, use this helper script:
# 
#   #!/bin/bash
#   # dump_all_workers.sh
#   MASTER_PID=$(pgrep -f "gunicorn.*channel_push")
#   for PID in $(pgrep -P $MASTER_PID); do
#       echo "Signaling worker $PID"
#       kill -USR1 $PID
#   done
#
# Or run: pkill -USR1 -P <master_pid>
