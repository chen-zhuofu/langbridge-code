#!/usr/bin/env python3
"""Domain-allowlist HTTP CONNECT proxy for eval containers (stdlib only).

Runs inside the ``lb-eval-proxy`` container, which sits on both the internal
eval network (reachable by agent containers) and the default bridge (has
internet access). Agent containers have no route out; their only egress is a
CONNECT tunnel through this proxy, which permits the LLM API host(s) only.

Denied hosts are logged to stdout — ``docker logs lb-eval-proxy`` shows any
attempt by an agent to reach a non-allowlisted host.

Env: ALLOWED_HOSTS (comma-separated hostnames), PORT (default 3128).
"""
import os
import socket
import threading

ALLOWED_HOSTS = {
    host.strip().lower()
    for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if host.strip()
}
PORT = int(os.environ.get("PORT", "3128"))


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(conn):
    try:
        conn.settimeout(15)
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(4096)
            if not chunk:
                return
            request += chunk
            if len(request) > 65536:
                return
        request_line = request.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = request_line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            print(f"deny non-CONNECT: {request_line!r}", flush=True)
            conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        host, _, port_text = parts[1].rpartition(":")
        host = (host or parts[1]).strip("[]").lower()
        port = int(port_text) if port_text.isdigit() else 443
        if host not in ALLOWED_HOSTS:
            print(f"deny {host}:{port}", flush=True)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        upstream = socket.create_connection((host, port), timeout=15)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        conn.settimeout(None)
        upstream.settimeout(None)
        threading.Thread(target=pipe, args=(upstream, conn), daemon=True).start()
        pipe(conn, upstream)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PORT))
    server.listen(128)
    print(f"egress proxy on :{PORT}, allowed: {sorted(ALLOWED_HOSTS)}", flush=True)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
