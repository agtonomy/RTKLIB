#!/usr/bin/env python3
"""str2str tcp inactivity-timeout regression tests (local loopback, no network).

Drives a str2str binary against fake NTRIP peers and asserts the inactivity
watchdog (waittcpcli/toinact in src/stream.c) fires only when it should:

  upload_silent    ntrip-server upload; caster handshakes then goes silent
                   -> MUST stay connected. strsvrthread reads every output
                      stream each cycle, so a receive-keyed watchdog falsely
                      reaped a healthy upload (see commit "role-aware ...").
  upload_dropped   ntrip-server upload; caster drops after the handshake
                   -> MUST recover via disconnect/send-error, not "timeout",
                      and not get stuck: a genuinely dead upload is detected.
  download_stall   ntrip-client download whose feed stalls while -n GGA is
                   written -> MUST time out despite the periodic GGA writes
                      (the one-way inbound stall fixed in bdb63c23).
  download_ok      ntrip-client download with a continuous feed
                   -> MUST stay connected.

Build the binary first, then run:

    make -C app/consapp/str2str/gcc
    python3 app/consapp/str2str/test/timeout_test.py

Options:
    timeout_test.py [BINARY] [--only NAME[,NAME...]] [--secs N]
                    [--toinact-ms N] [--disp-ms N]
Exits non-zero if any selected scenario fails.
"""
import argparse, os, socket, threading, time, subprocess, sys

DEFAULT_BIN = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gcc", "str2str"))

FEED, UP_SILENT, UP_DROP, DL_STALL, DL_OK = 6701, 6702, 6705, 6703, 6704
OUT = "/tmp/_str2str_relay.bin"


def server(port, handler):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(8)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handler, args=(c,), daemon=True).start()


def drain(c):
    try:
        while c.recv(65536):
            pass
    except OSError:
        pass


def feed(c):
    """Steady byte source for an upload's input stream."""
    try:
        pkt = b"\xd3" + bytes(1023)
        while True:
            c.sendall(pkt)
            time.sleep(0.2)
    except OSError:
        pass
    finally:
        c.close()


def caster_silent(c):
    """NTRIP server peer: ack handshake, then connected-but-silent forever."""
    try:
        c.recv(4096)
        c.sendall(b"ICY 200 OK\r\n")
        drain(c)
    except OSError:
        pass
    finally:
        c.close()


def caster_drop(c):
    """NTRIP server peer: ack handshake, then drop the connection."""
    try:
        c.recv(4096)
        c.sendall(b"ICY 200 OK\r\n")
    except OSError:
        pass
    finally:
        c.close()


def caster_download(stall):
    """NTRIP caster serving a client: handshake, feed, optionally stall."""
    def handler(c):
        try:
            c.recv(4096)                      # GET /MOUNT HTTP/1.0 ...
            c.sendall(b"ICY 200 OK\r\n")
            threading.Thread(target=drain, args=(c,), daemon=True).start()  # eat GGA
            pkt = b"\xd3" + bytes(255)
            for _ in range(5):                # initial feed: connection is receiving
                c.sendall(pkt)
                time.sleep(0.2)
            while True:
                if not stall:
                    c.sendall(pkt)            # healthy: keep streaming
                time.sleep(0.2)               # stall: silent but socket stays open
        except OSError:
            pass
        finally:
            c.close()
    return handler


def run(binary, args, secs, toinact, disp):
    full = [binary] + args + ["-s", str(toinact), "-r", "1000", "-d", str(disp)]
    p = subprocess.Popen(full, stderr=subprocess.PIPE, text=True)
    lines, deadline = [], time.time() + secs
    while time.time() < deadline:
        ln = p.stderr.readline()
        if not ln:
            break
        lines.append(ln.rstrip())
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
    return lines


def has(lines, sub):
    return any(sub in l for l in lines)


def n_timeouts(lines):
    return sum("timeout" in l for l in lines)


SCEN = {
    "upload_silent": dict(
        args=["-in", "tcpcli://127.0.0.1:%d" % FEED,
              "-out", "ntrips://:test@127.0.0.1:%d/UP" % UP_SILENT],
        check=lambda L: (n_timeouts(L) == 0 and has(L, "/UP"),
                         "timeouts=%d, connected=%s" % (n_timeouts(L), has(L, "/UP")))),
    "upload_dropped": dict(
        args=["-in", "tcpcli://127.0.0.1:%d" % FEED,
              "-out", "ntrips://:test@127.0.0.1:%d/UP" % UP_DROP],
        check=lambda L: (n_timeouts(L) == 0 and (has(L, "disconnect") or has(L, "error")),
                         "timeouts=%d, saw_disconnect/error=%s"
                         % (n_timeouts(L), has(L, "disconnect") or has(L, "error")))),
    "download_stall": dict(
        args=["-in", "ntrip://127.0.0.1:%d/DOWN" % DL_STALL, "-out", "file://" + OUT,
              "-n", "1000", "-p", "37.4", "-122.1", "30"],
        check=lambda L: (n_timeouts(L) >= 1 and has(L, "/DOWN"),
                         "timeouts=%d, connected=%s" % (n_timeouts(L), has(L, "/DOWN")))),
    "download_ok": dict(
        args=["-in", "ntrip://127.0.0.1:%d/DOWN" % DL_OK, "-out", "file://" + OUT,
              "-n", "1000", "-p", "37.4", "-122.1", "30"],
        check=lambda L: (n_timeouts(L) == 0 and has(L, "/DOWN"),
                         "timeouts=%d, connected=%s" % (n_timeouts(L), has(L, "/DOWN")))),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary", nargs="?", default=DEFAULT_BIN)
    ap.add_argument("--only", default="")
    ap.add_argument("--secs", type=int, default=12)
    ap.add_argument("--toinact-ms", type=int, default=2000)
    ap.add_argument("--disp-ms", type=int, default=500)
    a = ap.parse_args()

    if not os.path.exists(a.binary):
        sys.exit("binary not found: %s (build it: make -C app/consapp/str2str/gcc)"
                 % a.binary)

    for port, h in [(FEED, feed), (UP_SILENT, caster_silent), (UP_DROP, caster_drop),
                    (DL_STALL, caster_download(True)), (DL_OK, caster_download(False))]:
        threading.Thread(target=server, args=(port, h), daemon=True).start()

    names = [n for n in (a.only.split(",") if a.only else SCEN) if n]
    fails = 0
    for name in names:
        sc = SCEN[name]
        L = run(a.binary, sc["args"], a.secs, a.toinact_ms, a.disp_ms)
        ok, detail = sc["check"](L)
        print("[%s] %-15s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            for l in L[-6:]:
                print("      | " + l)
            fails += 1
    print("\n%d/%d passed (%s)" % (len(names) - fails, len(names), a.binary))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
