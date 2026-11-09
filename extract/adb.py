# -*- coding: utf-8 -*-
"""极简 adb 客户端（纯 socket，不依赖 adb.exe / adb 守护进程）

为什么不用 adb.exe：
  - 部分机器上 adb 服务进程起不来（安全策略拦截），`adb devices` 直接失败
  - 但模拟器自身已经监听了 adb 端口，协议本身照讲不误

所以这里直接实现 adb 协议的三个必要能力：CNXN 握手 / OPEN shell: / OPEN sync: + RECV。

用法：
    import adb
    print(adb.shell("ls /sdcard"))
    adb.pull_stream("/sdcard/a.bin", "a.bin")
"""

import os
import socket
import struct
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# MuMu 模拟器实例 0 的 adb 端口是 16384（不是常见的 16385/5555）
DEFAULT_HOST = os.environ.get("ADB_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("ADB_PORT", "16384"))

A_VERSION = 0x01000001
A_CLOSE = 0x01000001
A_SYNC = 0x01000002
FEATURES = b"host::features=shell_v2,cmd,stat_v2\x00"


def _checksum(data):
    return sum(data) & 0xFFFFFFFF


def _pack(cmd, arg1, arg2, data=b""):
    head = struct.pack("<4sIIII", cmd, arg1, arg2, len(data), _checksum(data))
    magic = struct.pack("<I", struct.unpack("<I", cmd)[0] ^ 0xFFFFFFFF)
    return head + magic + data


def _read_packet(sock):
    hdr = b""
    while len(hdr) < 24:
        chunk = sock.recv(24 - len(hdr))
        if not chunk:
            raise EOFError("connection closed while reading header")
        hdr += chunk
    cmd, arg1, arg2, dlen, _ = struct.unpack("<4sIIII", hdr[:20])
    data = b""
    while len(data) < dlen:
        chunk = sock.recv(min(dlen - len(data), 1 << 20))
        if not chunk:
            raise EOFError("connection closed while reading payload")
        data += chunk
    return cmd, arg1, arg2, data


def _connect(port, host, timeout):
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    sock.connect((host, port))
    sock.sendall(_pack(b"CNXN", A_VERSION, 4096, FEATURES))
    cmd, _, _, _ = _read_packet(sock)
    if cmd != b"CNXN":
        sock.close()
        raise RuntimeError("adb handshake failed: %r" % (cmd,))
    return sock


# --------------------------------------------------------------------------- shell

def shell(command, port=DEFAULT_PORT, host=DEFAULT_HOST, timeout=120):
    """在设备上执行 shell 命令，返回 stdout（合并 stderr）。"""
    sock = _connect(port, host, timeout)
    try:
        lid = 0x4455
        sock.sendall(_pack(b"OPEN", lid, 0, b"shell:" + command.encode() + b"\x00"))
        out = bytearray()
        while True:
            try:
                cmd, arg1, arg2, data = _read_packet(sock)
            except EOFError:
                break
            if cmd == b"OKAY":
                if data:
                    out += data
                continue
            if cmd == b"WRTE":
                out += data
                sock.sendall(_pack(b"OKAY", arg1, arg2))
                continue
            if cmd == b"CLSE":
                break
        return out.decode("utf-8", "replace")
    finally:
        sock.close()


# --------------------------------------------------------------------------- pull

def pull_stream(remote_path, local_path, port=DEFAULT_PORT, host=DEFAULT_HOST,
                timeout=3600, bufsize=8 << 20, quiet=False):
    """流式拉取文件（O(n) 状态机，边收边落盘，不缓存整包）。

    热点库动辄 1.9 GB，用 pyadb 那类"先收完再解析"的实现会退化成 O(n^2)，
    这里直接消费 sync: 协议的 DATA/DONE/FAIL 帧，8 MB 缓冲，实测 ~12 MB/s。
    """
    local_dir = os.path.dirname(os.path.abspath(local_path))
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)

    sock = _connect(port, host, timeout)
    total, elapsed = 0, 0.0
    try:
        lid = 0x3344
        sock.sendall(_pack(b"OPEN", lid, 0, b"sync:.\x00"))
        cmd, rid, _, _ = _read_packet(sock)
        if cmd != b"OKAY":
            raise RuntimeError("sync open failed: %r" % (cmd,))
        req = b"RECV" + struct.pack("<I", len(remote_path)) + remote_path.encode()
        sock.sendall(_pack(b"WRTE", lid, rid, req))

        pending = bytearray()
        done = False
        t0 = time.time()
        last = 0
        with open(local_path, "wb") as f:
            while not done:
                cmd, r1, r2, data = _read_packet(sock)
                if cmd == b"OKAY":
                    continue
                if cmd == b"WRTE":
                    pending += data
                    while len(pending) >= 8:
                        tag = bytes(pending[:4])
                        length = struct.unpack("<I", bytes(pending[4:8]))[0]
                        if tag == b"DATA":
                            if len(pending) < 8 + length:
                                break
                            f.write(pending[8:8 + length])
                            total += length
                            del pending[:8 + length]
                        elif tag == b"DONE":
                            done = True
                            break
                        elif tag == b"FAIL":
                            raise RuntimeError("sync FAIL: %r" % bytes(pending[8:8 + length]))
                        else:
                            raise RuntimeError("unknown sync tag %r" % tag)
                    sock.sendall(_pack(b"OKAY", lid, rid))
                    if not quiet and total - last > bufsize:
                        last = total
                        elapsed = time.time() - t0
                        print("  %.0f MB  %.1f MB/s" % (total / 1048576.0,
                                                        total / 1048576.0 / max(0.01, elapsed)),
                              flush=True)
                    continue
                if cmd == b"CLSE":
                    break
        elapsed = time.time() - t0
    finally:
        try:
            sock.sendall(_pack(b"CLSE", 0x3344, 0))
        except Exception:
            pass
        sock.close()
    return total, elapsed


def list_dirs(remote_path, port=DEFAULT_PORT, host=DEFAULT_HOST):
    return shell("ls -la " + remote_path, port=port, host=host)


if __name__ == "__main__":
    # 用法: python extract/adb.py "ls /sdcard/..."   （端口可用环境变量 ADB_PORT 覆盖）
    try:
        print(shell(" ".join(sys.argv[1:])))
    except (ConnectionRefusedError, OSError) as exc:
        print("连不上 %s:%d —— %s" % (DEFAULT_HOST, DEFAULT_PORT, exc))
        print("先启动模拟器，再确认实例的 adb 端口（MuMu 实例 0 = 16384），可用环境变量 ADB_PORT 覆盖")
        sys.exit(1)
