#!/usr/bin/env python3
"""Self-contained reproducer for urbit/vere#1100.

Pipelines N scry requests into a pier's .urb/conn.sock and closes the socket
without reading any of the replies, so the ship is left owing replies to a
socket that is gone.

    python3 repro_1100.py /path/to/pier [N]

Use a FAKE ship you can afford to lose. Needs nothing but python3.

Reads no replies on purpose: the point is to leave writes queued. Requests are
pipelined rather than sent one at a time, because a single small reply usually
fits in the socket buffer and is flushed before the close is noticed.
"""
import os, socket, sys, time

# ---- jam: noun -> bits.  int = atom, (head, tail) = cell.
#      Backreferences are omitted; that is a larger but valid encoding.

def jam(n):
    out = pos = 0
    def put(v, w):
        nonlocal out, pos
        out |= (v & ((1 << w) - 1)) << pos; pos += w
    def mat(a):
        if a == 0:
            put(1, 1); return
        b = a.bit_length(); c = b.bit_length()
        put(1 << c, c + 1)              # c zeros, then a one
        put(b & ((1 << (c - 1)) - 1), c - 1)
        put(a, b)
    def enc(x):
        if isinstance(x, tuple):
            put(1, 2)                   # cell: bit 1, then bit 0
            enc(x[0]); enc(x[1])
        else:
            put(0, 1); mat(x)           # atom: bit 0, then mat
    enc(n)
    return out.to_bytes(max(1, (pos + 7) // 8), 'little')

def newt(n):
    body = jam(n)
    return b'\x00' + len(body).to_bytes(4, 'little') + body

def tas(s):
    return int.from_bytes(s.encode(), 'little')

def path(*segs):
    n = 0
    for s in reversed(segs):
        n = (tas(s), n)
    return n

def peek(view, desk, *spur):
    """[%peek %| %once view desk spur]; an empty spur is /$"""
    sp = path(*spur) if spur else (0, 0)
    return (tas('peek'), (1, (tas('once'), (tas(view), (tas(desk) if desk else 0, sp)))))

def peel(*p):
    return (tas('peel'), path(*p))

# ---- the reproducer

pier = sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__)
count = int(sys.argv[2]) if len(sys.argv) > 2 else 200
sock = os.path.join(pier, '.urb', 'conn.sock')
if len(sock) > 100:
    sys.exit('AF_UNIX path limit: symlink the pier somewhere short and pass that')

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sock)
for i in range(count):
    s.sendall(newt((i, peek('e', 'bindings'))))
    s.sendall(newt((10000 + i, peek('bx', '', 'debug', 'timers'))))
s.close()
print('sent %d requests, closed the socket without reading any reply' % (count * 2))

time.sleep(10)
try:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(10); c.connect(sock)
    c.sendall(newt((1, peel('live'))))
    if not c.recv(64):
        raise ConnectionError('no reply to %peel /live')
    c.close()
    print('ship still answers -> PASS')
except Exception as e:
    print('ship does not answer: %r -> FAIL' % (e,))
    sys.exit(1)
