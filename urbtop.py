#!/usr/bin/env python3
"""urbtop: a bashtop/netdata-style dashboard for a running Urbit ship.

Reads ship internals through the pier's conn.sock (khan %peek = scries that
create no events; %peel = runtime metrics) plus /proc and the pier directory.
Only |mass (%peel /mass) blocks the serf, and only for ~1s on a slow interval.

Usage: urbtop.py PIER [--port 9909] [--log FILE] [--mass-interval 300]
"""
import argparse, json, os, sys, threading, time, glob, re, collections, socket, signal
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from noun import (newt_encode, cue_bytes, tas, untas, path, lst, seg, unpath, unmap, unset,
                  unit, da_to_unix, patp, parse_patp, clan)

HERE = os.path.dirname(os.path.abspath(__file__))
CLK = os.sysconf('SC_CLK_TCK')
HIST = 240          # samples kept per sparkline
FAST = 2.0          # seconds between cheap ticks
SLOW = 12.0         # seconds between the heavier scry sweep

_UV = '0123456789abcdefghijklmnopqrstuv'
def uv(n):
    if n == 0: return '0v0'
    digs = []
    while n:
        digs.append(_UV[n & 31]); n >>= 5
    s = ''.join(reversed(digs))
    out = []
    while len(s) > 5:
        out.insert(0, s[-5:]); s = s[:-5]
    out.insert(0, s)
    return '0v' + '.'.join(out)

def da(n):
    try: return da_to_unix(n)
    except Exception: return None

def cord(a):
    s = untas(a)
    return s if s is not None else str(a)

# ---------------------------------------------------------------- conn.sock

class Ship:
    """One persistent conn.sock connection for the life of the process.

    vere 4.6's conn.c segfaults the king if a client disconnects while a reply is
    still owed to it (took a production ship down twice on 2026-09-05, urbit/vere#1100). So: never
    open/close per request, never drop the socket on a slow reply (a timed-out
    request is simply ignored when its reply arrives), and on shutdown wait for
    every outstanding reply before closing. Replies are matched by rid, so many
    requests can be in flight at once."""
    def __init__(self, pier):
        self.sock_path = os.path.join(pier, '.urb', 'conn.sock')
        self.sock = None
        self.lock = threading.Lock()
        self.rid = 0
        self.pending = {}        # rid -> [event, result]; popped by the reader when the reply lands
        self.stopping = False

    def _connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        s.settimeout(None)
        self.sock = s
        threading.Thread(target=self._read_loop, args=(s,), daemon=True).start()

    def _read_loop(self, s):
        buf = bytearray()
        def rd(n):
            while len(buf) < n:
                chunk = s.recv(1 << 20)
                if not chunk: raise ConnectionError('conn.sock closed by ship')
                buf.extend(chunk)
            out = bytes(buf[:n]); del buf[:n]; return out
        try:
            while True:
                hdr = rd(5)
                msg = cue_bytes(rd(int.from_bytes(hdr[1:5], 'little')))
                slot = self.pending.pop(msg[0], None) if isinstance(msg, tuple) else None
                if slot: slot[1] = msg[1]; slot[0].set()
        except Exception as e:
            with self.lock:
                if self.sock is s: self.sock = None
                dead = list(self.pending.values()); self.pending.clear()
            for slot in dead: slot[1] = e; slot[0].set()
            try: s.close()
            except OSError: pass

    def request(self, cmd, timeout):
        if self.stopping: raise RuntimeError('urbtop is shutting down')
        with self.lock:
            if self.sock is None: self._connect()
            self.rid = rid = (self.rid + 1) & 0xffffffff
            slot = [threading.Event(), None]
            self.pending[rid] = slot
            try:
                self.sock.sendall(newt_encode((rid, cmd)))
            except OSError:
                self.pending.pop(rid, None)
                try: self.sock.close()
                except OSError: pass
                self.sock = None
                raise
        if not slot[0].wait(timeout):
            # leave the entry: the reply is still owed and must be read, not abandoned
            raise TimeoutError('conn.sock reply for %r still pending after %ss' % (cmd[0], timeout))
        if isinstance(slot[1], Exception): raise slot[1]
        return slot[1]

    def drain(self, limit=900):
        """stop issuing requests, wait for every outstanding reply, close."""
        self.stopping = True
        t0 = time.time()
        while self.pending and self.sock is not None and time.time() - t0 < limit:
            time.sleep(0.2)
        with self.lock:
            if self.sock is not None:
                try: self.sock.close()
                except OSError: pass
                self.sock = None
        return not self.pending

    def peel(self, *p, timeout=600):
        r = self.request((tas('peel'), path(*p)), timeout)
        return unit(r)                      # (unit *) -> noun or None

    def peek(self, view, desk, *spur, timeout=600):
        """khan %peek [%once view desk spur] -> (mark, noun) or None."""
        sp = path(*spur) if spur else (0, 0)  # gall vane scries need /$
        if spur == ('',): sp = (0, 0)
        cmd = (tas('peek'), (1, (tas('once'), (tas(view), (tas(desk) if desk else 0, sp)))))
        r = self.request(cmd, timeout)
        if r == 0 or not isinstance(r, tuple): return None
        res = r[1]                          # (unit (cask))  after %peek tag
        if res == 0: return None
        cask = res[1]
        if isinstance(cask, tuple) and cask[0] == tas('omen'):
            cask = cask[1][1]
        return cord(cask[0]), cask[1]

    def peekn(self, view, desk, *spur, timeout=600):
        r = self.peek(view, desk, *spur, timeout=timeout)
        return None if r is None else r[1]

# ------------------------------------------------------------ decoders

def mass_tree(n):
    """[cord (each * (list mass))] -> {name, value|children}"""
    name = cord(n[0]); e = n[1]
    if e[0] == 0:
        return {'name': name, 'value': e[1] if isinstance(e[1], int) else str(e[1])}
    return {'name': name, 'children': [mass_tree(x) for x in lst(e[1])]}

def quac(n):
    """[name size (list quac)] -> {name,size,children}"""
    kids = [quac(x) for x in lst(n[1][1])]
    kids.sort(key=lambda k: -k['size'])
    return {'name': cord(n[0]), 'size': n[1][0], 'children': kids}

def fields(n, k):
    out = []
    for _ in range(k - 1):
        out.append(n[0]); n = n[1]
    out.append(n)
    return out

def duct(n):
    return [unpath(w) for w in lst(n)]

def timers(n):
    out = []
    for t in lst(n):
        when, d = da(t[0]), duct(t[1])
        first = d[0] if d else '/'
        vane = first.split('/')[1] if '/' in first else '?'
        out.append({'at': when, 'wire': first, 'duct': d, 'vane': vane})
    out.sort(key=lambda x: x['at'] or 0)
    return out

def eyre_action(a):
    if isinstance(a, int): return cord(a)
    head = cord(a[0])
    if head == 'app': return 'app %s' % cord(a[1])
    if head == 'gen':
        try: return 'gen %s %s' % (cord(a[1][0]), unpath(a[1][1][0]))
        except Exception: return 'gen'
    return head

def eyre_bindings(n):
    out = []
    for b in lst(n):
        binding, rest = b[0], b[1]
        site = unit(binding[0]); pth = unpath(binding[1])
        out.append({'site': cord(site) if site is not None else '*', 'path': pth,
                    'action': eyre_action(rest[1]), 'duct': duct(rest[0])})
    out.sort(key=lambda x: (x['site'], x['path']))
    return out

def ship_list(n):
    return [patp(s) for s in lst(n)]

# ------------------------------------------------------------ /proc helpers

def read(p, default=''):
    try:
        with open(p) as f: return f.read()
    except OSError: return default

def proc_stat(pid):
    s = read('/proc/%d/stat' % pid)
    if not s: return None
    rp = s.rindex(')'); f = s[rp + 2:].split()
    st = {'state': f[0], 'utime': int(f[11]), 'stime': int(f[12]), 'threads': int(f[17]),
          'starttime': int(f[19]), 'vsize': int(f[20]), 'rss': int(f[21]) * os.sysconf('SC_PAGE_SIZE'),
          'minflt': int(f[7]), 'majflt': int(f[9])}
    status = read('/proc/%d/status' % pid)
    for k in ('VmRSS', 'VmSwap', 'VmHWM', 'voluntary_ctxt_switches', 'nonvoluntary_ctxt_switches'):
        m = re.search(r'^%s:\s+(\d+)' % k, status, re.M)
        if m: st[k] = int(m.group(1))
    try: st['fds'] = len(os.listdir('/proc/%d/fd' % pid))
    except OSError: st['fds'] = None
    io = read('/proc/%d/io' % pid)
    for k in ('read_bytes', 'write_bytes', 'rchar', 'wchar'):
        m = re.search(r'^%s: (\d+)' % k, io, re.M)
        if m: st[k] = int(m.group(1))
    return st

def find_pids(pier):
    """serf = the 'work' process whose --snap-dir is this pier; king = its parent."""
    king = serf = None
    for p in os.listdir('/proc'):
        if not p.isdigit(): continue
        cmd = read('/proc/%s/cmdline' % p).split('\0')
        if 'work' in cmd and '--snap-dir' in cmd:
            try:
                if os.path.realpath(cmd[cmd.index('--snap-dir') + 1]) != os.path.realpath(pier): continue
                serf = int(p)
                s = read('/proc/%s/stat' % p)
                king = int(s[s.rindex(')') + 2:].split()[1])
                break
            except (ValueError, IndexError): pass
    return king, serf

def dir_bytes(p):
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try: total += os.stat(os.path.join(root, f)).st_size
            except OSError: pass
    return total

def host_stats():
    mem = {}
    for line in read('/proc/meminfo').splitlines():
        k, v = line.split(':', 1); mem[k] = int(v.split()[0]) * 1024
    load = read('/proc/loadavg').split()
    up = float(read('/proc/uptime').split()[0] or 0)
    return {'load': [float(x) for x in load[:3]], 'mem_total': mem.get('MemTotal'),
            'mem_avail': mem.get('MemAvailable'), 'swap_total': mem.get('SwapTotal'),
            'swap_free': mem.get('SwapFree'), 'uptime': up, 'ncpu': os.cpu_count()}

def tail_lines(fn, n=300, maxbytes=256 * 1024):
    try:
        with open(fn, 'rb') as f:
            f.seek(0, 2); size = f.tell()
            f.seek(max(0, size - maxbytes)); data = f.read()
    except OSError: return []
    text = re.sub(rb'\x1b\[[0-9;?]*[A-Za-z]|\r', b'', data).decode('utf-8', 'replace')
    lines = [l for l in text.split('\n') if l.strip()]
    return lines[-n:]

# ------------------------------------------------------------ collector

class Collector:
    def __init__(self, pier, log, mass_interval):
        self.pier = os.path.abspath(pier)
        self.ship = Ship(self.pier)
        self.log = log
        self.mass_interval = mass_interval
        self.state = {'ts': 0, 'errors': {}}
        self.hist = collections.defaultdict(lambda: collections.deque(maxlen=HIST))
        self.prev = {}
        self.mass_now = threading.Event()
        self.lock = threading.Lock()

    def err(self, key, e):
        self.state.setdefault('errors', {})[key] = '%s: %s' % (type(e).__name__, e)

    def ok(self, key):
        self.state.get('errors', {}).pop(key, None)

    def run(self):
        threading.Thread(target=self.mass_loop, daemon=True).start()
        last_slow = 0
        while True:
            t0 = time.time()
            self.fast_tick()
            if t0 - last_slow >= SLOW:
                self.slow_tick(); last_slow = t0
            self.state['ts'] = time.time()
            self.state['tick_ms'] = int((time.time() - t0) * 1000)
            time.sleep(max(0.2, FAST - (time.time() - t0)))

    # ---- fast: runtime counters, procs, timers, live-ish things
    def fast_tick(self):
        st = self.state; now = time.time()
        try:
            info = self.ship.peel('info')
            st['info'] = mass_tree(info)
            ev = self._find(st['info'], ['lord', 'event'])
            st['event'] = ev
            if 'ev' in self.prev and ev is not None:
                dt = now - self.prev['ev_t']
                rate = (ev - self.prev['ev']) / dt if dt > 0 else 0
                self.hist['events'].append(round(rate, 2))
            self.prev['ev'], self.prev['ev_t'] = ev, now
            st['live'] = True; self.ok('runtime')
        except Exception as e:
            st['live'] = False; self.err('runtime', e)

        # processes
        try:
            king, serf = find_pids(self.pier)
            procs = {}
            for name, pid in (('king', king), ('serf', serf)):
                if not pid: continue
                s = proc_stat(pid)
                if not s: continue
                s['pid'] = pid
                key = 'cpu_' + name
                cpu = (s['utime'] + s['stime']) / CLK
                if key in self.prev:
                    pc, pt = self.prev[key]
                    s['cpu'] = round(100 * (cpu - pc) / max(1e-6, now - pt), 1)
                    self.hist[key].append(s['cpu'])
                self.prev[key] = (cpu, now)
                s['uptime'] = now - self._boot_time() - s['starttime'] / CLK
                if name == 'serf':
                    cmd = read('/proc/%d/cmdline' % pid).split('\0')
                    if '--loom' in cmd: st['loom'] = 2 ** int(cmd[cmd.index('--loom') + 1])
                procs[name] = s
                self.hist['rss_' + name].append(s['rss'])
            st['procs'] = procs; self.ok('procs')
        except Exception as e:
            self.err('procs', e)

        try:
            st['host'] = host_stats()
            self.hist['load'].append(st['host']['load'][0])
        except Exception as e:
            self.err('host', e)

        # behn timers (cheap, and the thing people most want to watch)
        try:
            t = self.ship.peekn('bx', '', 'debug', 'timers', timeout=60)
            tl = timers(t) if t is not None else []
            by_vane = collections.Counter(x['vane'] for x in tl)
            # ponytail: browser gets the soonest 500; a real ship can hold thousands of ames pump timers
            st['behn'] = {'timers': tl[:500], 'count': len(tl), 'by_vane': dict(by_vane.most_common()), 'now': now}
            self.hist['timers'].append(len(tl))
            self.ok('behn')
        except Exception as e:
            self.err('behn', e)

        # spider threads, eyre live state, dill sessions
        try:
            tr = self.ship.peekn('gx', 'spider', 'tree', 'noun')
            st['spider'] = ['/'.join(uv(t) for t in lst(x)) for x in lst(tr)] if tr is not None else []
            self.hist['threads'].append(len(st['spider']))
            self.ok('spider')
        except Exception as e:
            self.err('spider', e)
        try:
            ey = st.setdefault('eyre', {})
            conns = self.ship.peekn('e', 'connections')
            ey['connections'] = len(unmap(conns)) if conns is not None else 0
            # ponytail: no %e /channel-state peek: on a real ship that noun is every
            # channel's event backlog (hundreds of MB), and the serf jams it. Count later if a cheap path appears.
            chans = []
            ey['channels'] = chans
            self.hist['channels'].append(len(chans))
            self.ok('eyre-live')
        except Exception as e:
            self.err('eyre-live', e)
        try:
            ds = self.ship.peekn('dy', '', 'sessions')
            st['dill'] = {'sessions': [cord(x) or '$' for x in unset(ds)]} if ds is not None else {}
        except Exception as e:
            self.err('dill', e)

        # pier on disk
        try:
            urb = os.path.join(self.pier, '.urb')
            chk = sorted(glob.glob(os.path.join(urb, 'chk', '*.bin')))
            chk_m = max((os.stat(f).st_mtime for f in chk), default=None)
            epochs = sorted(d for d in os.listdir(os.path.join(urb, 'log')) if d.startswith('0i'))
            sv = os.statvfs(self.pier)
            st['pier'] = {'path': self.pier, 'log_bytes': dir_bytes(os.path.join(urb, 'log')),
                          'chk_bytes': sum(os.stat(f).st_size for f in chk), 'chk_mtime': chk_m,
                          'epochs': epochs, 'disk_free': sv.f_bavail * sv.f_frsize,
                          'disk_total': sv.f_blocks * sv.f_frsize,
                          'put_files': len(os.listdir(os.path.join(urb, 'put'))) if os.path.isdir(os.path.join(urb, 'put')) else 0}
            self.ok('pier')
        except Exception as e:
            self.err('pier', e)

        if self.log:
            st['log'] = tail_lines(self.log)

        st['hist'] = {k: list(v) for k, v in self.hist.items()}

    # ---- slow: identity, desks, agents, peers, eyre config
    def slow_tick(self):
        st = self.state; sh = self.ship
        try:
            who = sh.peel('who'); st['who'] = patp(who); st['who_num'] = who; st['clan'] = clan(who)
            v = sh.peel('v'); st['vere'] = cord(v)
            ports = {}
            for p in ('ames', 'http', 'htls'):
                try: ports[p] = sh.peel('port', p)
                except Exception: ports[p] = None
            st['ports'] = ports
        except Exception as e:
            self.err('identity', e)
        me = st.get('who', '~zod')
        try:
            j = {}
            j['life'] = unit(sh.peekn('j', 'lyfe', me))
            j['rift'] = sh.peekn('j', 'rift', me)
            j['fake'] = sh.peekn('j', 'fake') == 0
            sein = sh.peekn('j', 'sein', me); j['sein'] = patp(sein) if sein is not None else None
            saxo = sh.peekn('j', 'saxo', me); j['saxo'] = ship_list(saxo) if saxo is not None else []
            turf = sh.peekn('j', 'turf'); j['turf'] = ['.'.join(cord(x) for x in lst(t)) for t in lst(turf)] if turf is not None else []
            st['jael'] = j; self.ok('jael')
        except Exception as e:
            self.err('jael', e)

        # clay desks + kiln
        try:
            desks = {}
            tire = sh.peekn('cx', '', 'tire')
            for desk, v in (unmap(tire) if tire is not None else []):
                desks[cord(desk)] = {'zest': cord(v[0]), 'wic': ['%s/%d' % (cord(w[0]), w[1]) for w in unset(v[1])]}
            pikes = sh.peekn('gx', 'hood', 'kiln', 'pikes', 'noun')
            for desk, pk in (unmap(pikes) if pikes is not None else []):
                d = desks.setdefault(cord(desk), {})
                sync = unit(pk[0])
                d['sync'] = '%s/%s' % (patp(sync[0]), cord(sync[1])) if sync is not None else None
                d['hash'] = uv(pk[1][0])[:12]
                d.setdefault('zest', cord(pk[1][1][0]))
            for name, d in desks.items():
                try:
                    c = sh.peekn('cw', name)
                    if c is not None: d['rev'], d['rev_at'] = c[0], da(c[1])
                    k = sh.peekn('cx', name, 'sys', 'kelvin')
                    if k is not None:   # weft [%zuse 408] or waft [[%1 ~] (set weft)]
                        wefts = [k] if isinstance(k[0], int) else unset(k[1]) if k[0] == (1, 0) else lst(k)
                        d['kelvin'] = ', '.join('%s %d' % (cord(w[0]), w[1]) for w in sorted(wefts, key=lambda w: -w[1]))
                    b = sh.peekn('cx', name, 'desk', 'bill')
                    d['bill'] = [cord(x) for x in lst(b)] if b is not None else []
                    dk = sh.peekn('cx', name, 'desk', 'docket-0')
                    if dk is not None:   # [%1 title info color href image version website license]
                        f = fields(dk[1], 8)
                        d['title'] = cord(f[0]); d['version'] = '.'.join(str(x) for x in fields(f[5], 3))
                except Exception as e:
                    d['error'] = str(e)
            st['clay'] = {'desks': desks}
            syncs = sh.peekn('gx', 'hood', 'kiln', 'syncs', 'noun')
            st['kiln'] = {'syncs': [{'desk': cord(k[0]), 'from': '%s/%s' % (patp(k[1][0]), cord(k[1][1])),
                                     'nonce': cord(v[0]), 'let': v[1][1][0]} for k, v in (unmap(syncs) if syncs is not None else [])]}
            self.ok('clay')
        except Exception as e:
            self.err('clay', e)

        # gall agents per desk
        try:
            agents = []
            nonces = {}
            nn = sh.peekn('gf', 'base', '')
            if nn is not None: nonces = {cord(k): v for k, v in unmap(nn)}
            for name in st.get('clay', {}).get('desks', {}):
                apps = sh.peekn('ge', name, '')
                if apps is None: continue
                for a in unset(apps):
                    dude = cord(a[0])
                    agents.append({'desk': name, 'name': dude, 'live': a[1] == 0, 'nonce': nonces.get(dude)})
            agents.sort(key=lambda a: (a['desk'], a['name']))
            st['gall'] = {'agents': agents}
            self.hist['agents'].append(sum(1 for a in agents if a['live']))
            self.ok('gall')
        except Exception as e:
            self.err('gall', e)

        # ames
        try:
            a = {}
            a['rift'] = sh.peekn('ax', '', 'rift')
            a['protocol'] = sh.peekn('ax', '', 'protocol', 'version')
            sn = sh.peekn('ax', '', 'snubbed')
            a['snub'] = {'form': cord(sn[0]), 'ships': ship_list(sn[1])} if sn is not None else None
            peers = sh.peekn('ax', '', 'peers', timeout=60)
            allp = sorted(unmap(peers)) if peers is not None else []
            a['peer_count'] = len(allp)
            a['known_count'] = sum(1 for _, s_ in allp if cord(s_) == 'known')
            a['by_clan'] = dict(collections.Counter(clan(n) for n, _ in allp))
            plist = []
            # ponytail: per-peer detail is 4 small peeks each; only the 300 lowest-numbered
            # (galaxies, stars, then planets) get it. Paging/selection if someone needs more.
            for num, state in allp[:300]:
                p = {'ship': patp(num), 'clan': clan(num), 'state': cord(state)}
                if p['state'] == 'known':
                    try:
                        lc = unit(sh.peekn('ax', '', 'peers', p['ship'], 'last-contact'))
                        p['last_contact'] = da(lc) if lc is not None else None
                        bones = sh.peekn('ax', '', 'bones', p['ship'])
                        p['snd'], p['rcv'] = len(unset(bones[0])), len(unset(bones[1]))
                        p['corked'] = len(unset(sh.peekn('ax', '', 'corked', p['ship']) or 0))
                        p['closing'] = len(unset(sh.peekn('ax', '', 'closing', p['ship']) or 0))
                    except Exception as e:
                        p['error'] = str(e)
                plist.append(p)
            a['peers'] = plist
            ch = sh.peekn('ax', '', 'chums')
            a['chums'] = len(unmap(ch)) if ch is not None else None
            st['ames'] = a
            self.hist['peers'].append(len(allp))
            self.ok('ames')
        except Exception as e:
            self.err('ames', e)

        # eyre config
        try:
            ey = st.setdefault('eyre', {})
            b = sh.peekn('e', 'bindings'); ey['bindings'] = eyre_bindings(b) if b is not None else []
            c = sh.peekn('ex', '', 'cors')
            if c is not None:
                ey['cors'] = {'requests': [cord(x) for x in unset(c[0])], 'approved': [cord(x) for x in unset(c[1][0])],
                              'rejected': [cord(x) for x in unset(c[1][1])]}
            h = sh.peekn('e', 'host')
            if h is not None:
                port = unit(h[1][0]); host = h[1][1]
                ey['host'] = '%s://%s%s' % ('https' if h[0] == 0 else 'http',
                                            '.'.join(reversed([cord(x) for x in lst(host[1])])) if host[0] == 0 else 'ip',
                                            ':%d' % port if port is not None else '')
            p = sh.peekn('e', 'ports')
            if p is not None: ey['ports'] = {'insecure': p[0], 'secure': unit(p[1])}
            d = sh.peekn('e', 'domains')
            ey['domains'] = ['.'.join(reversed([cord(x) for x in lst(t)])) for t in unset(d)] if d is not None else []
            self.ok('eyre')
        except Exception as e:
            self.err('eyre', e)

    def mass_loop(self):
        time.sleep(4)
        while True:
            if self.mass_interval > 0 or self.mass_now.is_set():
                self.mass_now.clear()
                try:
                    t0 = time.time()
                    m = self.ship.peel('mass', timeout=900)   # blocks the serf (1s on a 2GB loom, longer on big ones)
                    # /quic is cheap but prints open:/idle: on the ship console, so only alongside |mass
                    q = self.ship.peel('quic')
                    self.state['quic'] = {cord(x[0]): x[1] for x in lst(q[1])} if q else {}
                    if 'open' in self.state['quic']: self.hist['loom_open'].append(self.state['quic']['open'])
                    kids = [quac(x) for x in lst(m[1])]
                    tree = {'name': 'mass', 'size': sum(k['size'] for k in kids), 'children': kids}
                    self.state['mass'] = {'ts': time.time(), 'took_ms': int((time.time() - t0) * 1000), 'tree': tree}
                    self.hist['mass_total'].append(tree['size'])
                    self.ok('mass')
                except Exception as e:
                    self.err('mass', e)
            wait = self.mass_interval if self.mass_interval > 0 else 3600
            self.mass_now.wait(wait)

    def _find(self, tree, keys):
        for k in keys:
            if 'children' not in tree: return None
            nxt = next((c for c in tree['children'] if c['name'] == k), None)
            if nxt is None: return None
            tree = nxt
        return tree.get('value')

    _bt = None
    def _boot_time(self):
        if self._bt is None:
            m = re.search(r'^btime (\d+)', read('/proc/stat'), re.M)
            self._bt = int(m.group(1)) if m else 0
        return self._bt

# ------------------------------------------------------------ http

class Handler(BaseHTTPRequestHandler):
    col = None
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path.startswith('/api/state'):
            self._send(200, json.dumps(self.col.state, default=str).encode(), 'application/json')
        elif self.path == '/' or self.path.startswith('/index'):
            with open(os.path.join(HERE, 'urbtop.html'), 'rb') as f: self._send(200, f.read(), 'text/html; charset=utf-8')
        else:
            self._send(404, b'not found', 'text/plain')
    def do_POST(self):
        if self.path == '/api/mass':
            self.col.mass_now.set(); self._send(200, b'{"ok":true}', 'application/json')
        else:
            self._send(404, b'not found', 'text/plain')

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pier')
    ap.add_argument('--port', type=int, default=9909)
    ap.add_argument('--bind', default='127.0.0.1')
    ap.add_argument('--log', help='file receiving the ship\'s console output (e.g. via tmux pipe-pane)')
    ap.add_argument('--mass-interval', type=int, default=300, help='seconds between |mass reports; 0 = manual only')
    a = ap.parse_args()
    if not os.path.exists(os.path.join(a.pier, '.urb', 'conn.sock')):
        sys.exit('no conn.sock under %s/.urb — is the ship running?' % a.pier)
    col = Collector(a.pier, a.log, a.mass_interval)
    Handler.col = col
    threading.Thread(target=col.run, daemon=True).start()
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    def shutdown(signum, frame):
        print('urbtop: signal %d, draining conn.sock (%d pending)' % (signum, len(col.ship.pending)), flush=True)
        threading.Thread(target=lambda: (col.ship.drain(), os._exit(0)), daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown); signal.signal(signal.SIGINT, shutdown)
    print('urbtop: http://%s:%d/  pier=%s' % (a.bind, a.port, col.pier), flush=True)
    srv.serve_forever()

if __name__ == '__main__':
    main()
