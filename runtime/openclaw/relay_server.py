"""Trusted audit sidecar; never mounted in or shared with the agent container."""
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import signal
import threading
import upstream_audit_proxy as proxy

server=object.__new__(proxy.AuditHTTPServer)
server.client_token=os.environ['MHB_CLIENT_TOKEN']
server.upstream_key=os.environ['PILOT_QR_AUDIT_UPSTREAM_KEY']
server.log_path=Path('/audit/upstream_audit.jsonl')
server.upstream_timeout=900
server.expected_marker=''
ThreadingHTTPServer.__init__(server,('0.0.0.0',17843),proxy.AuditHandler)
signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
try:server.serve_forever(poll_interval=.2)
finally:server.server_close()
