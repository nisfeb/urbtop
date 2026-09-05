#!/usr/bin/env python3
"""Reproducer for urbit/vere#1100: a conn.sock client that vanishes while the
ship still owes it replies. On an unpatched vere 4.6 the king segfaults.

Usage: tests/repro_1100.py /path/to/pier   (a FAKE ship you can afford to lose)
"""
import os, socket, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from noun import newt_encode, tas, path, conn_request, show

pier = sys.argv[1]
sock = os.path.join(pier, '.urb', 'conn.sock')

def peek_once(view, desk, *spur):
    sp = path(*spur) if spur else (0, 0)
    return (tas('peek'), (1, (tas('once'), (tas(view), (tas(desk) if desk else 0, sp)))))

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sock)
for i in range(200):
    s.sendall(newt_encode((i, peek_once('e', 'bindings'))))
    s.sendall(newt_encode((1000 + i, peek_once('bx', '', 'debug', 'timers'))))
s.close()
print('closed the socket with ~400 replies owed; waiting 10s')
time.sleep(10)
try:
    r = conn_request(sock, (7, (tas('peel'), path('live'))), timeout=10)
    print('ship still answers:', show(r), '-> PASS')
except Exception as e:
    print('ship does not answer:', repr(e), '-> FAIL (crashed?)')
    sys.exit(1)
