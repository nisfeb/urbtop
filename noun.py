"""Minimal Urbit noun codec: jam/cue, newt framing over conn.sock, and
@p / @da / @tas / path / treap decoding.  Nouns: int = atom, (h, t) = cell."""
import socket, datetime

# ---- jam / cue --------------------------------------------------------------

def jam(n):
    out, pos, seen = 0, 0, {}
    def put(bits, width):
        nonlocal out, pos
        out |= bits << pos; pos += width
    def mat(a):
        if a == 0:
            put(1, 1); return
        b = a.bit_length(); c = b.bit_length()
        put(1 << c, c + 1)
        put(b & ((1 << (c - 1)) - 1), c - 1)
        put(a, b)
    todo = [n]
    while todo:
        x = todo.pop()
        if isinstance(x, tuple):
            if id(x) in seen:
                put(3, 2); mat(seen[id(x)]); continue
            seen[id(x)] = pos
            put(1, 2)
            todo.append(x[1]); todo.append(x[0])
        else:
            put(0, 1); mat(x)
    return out, pos

def cue_bytes(b):
    """linear-time cue over a byte buffer (bit slicing a big int is O(n^2))."""
    pos, seen = 0, {}
    nbits = len(b) * 8
    def bit():
        nonlocal pos
        v = (b[pos >> 3] >> (pos & 7)) & 1; pos += 1; return v
    def bits(w):
        nonlocal pos
        if w == 0: return 0
        start = pos >> 3; end = (pos + w + 7) >> 3
        v = (int.from_bytes(b[start:end], 'little') >> (pos & 7)) & ((1 << w) - 1)
        pos += w; return v
    def rub():
        c = 0
        while bit() == 0:
            c += 1
            if pos > nbits: raise ValueError("bad jam")
        if c == 0: return 0
        n = bits(c - 1) | (1 << (c - 1))
        return bits(n)
    root = [None]
    stack = [('v', root, 0)]
    while stack:
        fr = stack.pop()
        if fr[0] == 'c':
            _, dst, idx, hd, tl, start = fr
            v = (hd[0], tl[0]); seen[start] = v; dst[idx] = v; continue
        _, dst, idx = fr
        start = pos
        if bit() == 0:
            v = rub(); seen[start] = v; dst[idx] = v
        elif bit() == 0:
            hd, tl = [None], [None]
            stack.append(('c', dst, idx, hd, tl, start))
            stack.append(('v', tl, 0)); stack.append(('v', hd, 0))
        else:
            dst[idx] = seen[rub()]
    return root[0]

def cue(a):
    return cue_bytes(a.to_bytes(max(1, (a.bit_length() + 7) // 8), 'little'))

def jam_bytes(n):
    v, nb = jam(n)
    return v.to_bytes(max(1, (nb + 7) // 8), 'little')

# ---- newt framing + conn.sock --------------------------------------------------

def newt_encode(n):
    body = jam_bytes(n)
    return b'\x00' + len(body).to_bytes(4, 'little') + body

def conn_request(sock_path, req, timeout=10.0):
    """Send a [rid command ...] noun to conn.sock; return the decoded reply noun."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall(newt_encode(req))
        buf = bytearray()
        def rd(n):
            while len(buf) < n:
                chunk = s.recv(min(1 << 20, n - len(buf)))
                if not chunk: raise ConnectionError("conn.sock closed")
                buf.extend(chunk)
            out = bytes(buf[:n]); del buf[:n]; return out
        hdr = rd(5)
        return cue_bytes(rd(int.from_bytes(hdr[1:5], 'little')))
    finally:
        s.close()

# ---- atoms ---------------------------------------------------------------------

def tas(s):
    return int.from_bytes(s.encode(), 'little')

def untas(a):
    if not isinstance(a, int): return None
    try: return a.to_bytes((a.bit_length() + 7) // 8, 'little').decode()
    except UnicodeDecodeError: return None

def path(*segs):
    n = 0
    for s in reversed(segs):
        n = ((tas(s) if isinstance(s, str) else s), n)
    return n

def lst(n):
    out = []
    while n != 0:
        out.append(n[0]); n = n[1]
    return out

def seg(a):
    s = untas(a)
    return s if s is not None and s.isprintable() else '0x%x' % a

def unpath(n):
    return '/' + '/'.join(seg(x) for x in lst(n))

def tree_items(n):
    out, todo = [], [n]
    while todo:
        x = todo.pop()
        if x == 0: continue
        out.append(x[0]); todo.append(x[1][0]); todo.append(x[1][1])
    return out

unset = tree_items
def unmap(n):
    return [(kv[0], kv[1]) for kv in tree_items(n)]

def unit(n):
    return None if n == 0 else n[1]

# @da: 128 bits, high 64 = seconds since ~292277024401-.1.1, low 64 = fraction
DA_UNIX = 0x8000000cce9e0d80
def da_to_unix(da):
    return ((da >> 64) - DA_UNIX) + (da & ((1 << 64) - 1)) / 2**64
def unix_to_da(t):
    s = int(t); f = int((t - s) * 2**64)
    return ((s + DA_UNIX) << 64) | f
def da_str(da):
    try:
        return datetime.datetime.fromtimestamp(da_to_unix(da), datetime.timezone.utc).isoformat(timespec='milliseconds')
    except (OverflowError, ValueError, OSError):
        return '~' + str(da)

# ---- @p ------------------------------------------------------------------------
_PRE = ("dozmarbinwansamlitsighidfidlissogdirwacsabwissibrigsoldopmodfoglidhopdardorlorhodfolrintogsilmir"
"holpaslacrovlivdalsatlibtabhanticpidtorbolfosdotlosdilforpilramtirwintadbicdifrocwidbisdasmidlop"
"rilnardapmolsanlocnovsitnidtipsicropwitnatpanminritpodmottamtolsavposnapnopsomfinfonbanmorworsip"
"ronnorbotwicsocwatdolmagpicdavbidbaltimtasmalligsivtagpadsaldivdactansidfabtarmonranniswolmispal"
"lasdismaprabtobrollatlonnodnavfignomnibpagsopralbilhaddocridmocpacravripfaltodtiltinhapmicfanpat"
"taclabmogsimsonpinlomrictapfirhasbosbatpochactidhavsaplindibhosdabbitbarracparloddosbortochilmac"
"tomdigfilfasmithobharmighinradmashalraglagfadtopmophabnilnosmilfopfamdatnoldinhatnacrisfotribhoc"
"nimlarfitwalrapsarnalmoslandondanladdovrivbacpollaptalpitnambonrostonfodponsovnocsorlavmatmipfip")
_SUF = ("zodnecbudwessevpersutletfulpensytdurwepserwylsunrypsyxdyrnuphebpeglupdepdysputlughecryttyvsydnex"
"lunmeplutseppesdelsulpedtemledtulmetwenbynhexfebpyldulhetmevruttylwydtepbesdexsefwycburderneppur"
"rysrebdennutsubpetrulsynregtydsupsemwynrecmegnetsecmulnymtevwebsummutnyxrextebfushepbenmuswyxsym"
"selrucdecwexsyrwetdylmynmesdetbetbeltuxtugmyrpelsyptermebsetdutdegtexsurfeltudnuxruxrenwytnubmed"
"lytdusnebrumtynseglyxpunresredfunrevrefmectedrusbexlebduxrynnumpyxrygryxfeptyrtustyclegnemfermer"
"tenlusnussyltecmexpubrymtucfyllepdebbermughuttunbylsudpemdevlurdefbusbeprunmelpexdytbyttyplevmyl"
"wedducfurfexnulluclennerlexrupnedlecrydlydfenwelnydhusrelrudneshesfetdesretdunlernyrsebhulryllud"
"remlysfynwerrycsugnysnyllyndyndemluxfedsedbecmunlyrtesmudnytbyrsenwegfyrmurtelreptegpecnelnevfes")
PRE = [_PRE[i:i+3] for i in range(0, 768, 3)]
SUF = [_SUF[i:i+3] for i in range(0, 768, 3)]
assert len(PRE) == 256 and len(SUF) == 256

def _murmur3(data, seed):
    c1, c2 = 0xcc9e2d51, 0x1b873593
    h = seed & 0xffffffff
    ln = len(data)
    for i in range(0, ln - ln % 4, 4):
        k = int.from_bytes(data[i:i+4], 'little')
        k = (k * c1) & 0xffffffff; k = ((k << 15) | (k >> 17)) & 0xffffffff; k = (k * c2) & 0xffffffff
        h ^= k; h = ((h << 13) | (h >> 19)) & 0xffffffff; h = (h * 5 + 0xe6546b64) & 0xffffffff
    tail = data[ln - ln % 4:]
    k = 0
    if len(tail) >= 3: k ^= tail[2] << 16
    if len(tail) >= 2: k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xffffffff; k = ((k << 15) | (k >> 17)) & 0xffffffff; k = (k * c2) & 0xffffffff
        h ^= k
    h ^= ln
    h ^= h >> 16; h = (h * 0x85ebca6b) & 0xffffffff
    h ^= h >> 13; h = (h * 0xc2b2ae35) & 0xffffffff
    h ^= h >> 16
    return h

_RAKU = [0xb76d5eed, 0xee281300, 0x85bcae01, 0x4b387af7]
def _F(j, arg):
    return _murmur3(bytes([arg & 0xff, (arg >> 8) & 0xff]), _RAKU[j])

def _fe(r, a, b, m):
    ell, arr = m % a, m // a
    for j in range(1, r + 1):
        eff = _F(j - 1, arr)
        tmp = (ell + eff) % (a if j % 2 else b)
        ell, arr = arr, tmp
    if r % 2:
        return a * arr + ell
    return a * arr + ell if arr == a else a * ell + arr

def _fen(r, a, b, m):
    ahh = m // a if r % 2 else m % a
    ale = m % a if r % 2 else m // a
    ell, arr = (ahh, ale) if ale == a else (ale, ahh)
    for j in range(r, 0, -1):
        eff = _F(j - 1, ell)
        tmp = (arr + (a if j % 2 else b) - eff % (a if j % 2 else b)) % (a if j % 2 else b)
        ell, arr = tmp, ell
    return a * arr + ell

def _feis(m):
    c = _fe(4, 65535, 65536, m)
    return c if c < 0xffffffff else _fe(4, 65535, 65536, c)
def _tail(m):
    c = _fen(4, 65535, 65536, m)
    return c if c < 0xffffffff else _fen(4, 65535, 65536, c)

def fein(p):
    if 0x10000 <= p <= 0xffffffff: return 0x10000 + _feis(p - 0x10000)
    if 0x100000000 <= p <= 0xffffffffffffffff: return (p & ~0xffffffff) | fein(p & 0xffffffff)
    return p
def fynd(p):
    if 0x10000 <= p <= 0xffffffff: return 0x10000 + _tail(p - 0x10000)
    if 0x100000000 <= p <= 0xffffffffffffffff: return (p & ~0xffffffff) | fynd(p & 0xffffffff)
    return p

def patp(p):
    sxz = fein(p)
    if sxz < 256: return '~' + SUF[sxz]
    bs = sxz.to_bytes(max(2, (sxz.bit_length() + 7) // 8), 'big')
    if len(bs) % 2: bs = b'\x00' + bs
    parts = []
    for i in range(0, len(bs), 2):
        parts.append(PRE[bs[i]] + SUF[bs[i+1]])
    # group as --- separated every 4 syllable-pairs? ~sampel-palnet-... uses '-' between pairs, '--' every 4 pairs
    out = []
    for i, part in enumerate(reversed(parts)):
        out.append(part)
    out = list(reversed(out))
    s = ''
    for i, part in enumerate(parts):
        n = len(parts) - i
        s += part
        if i != len(parts) - 1:
            s += '--' if n % 4 == 1 else '-'
    return '~' + s

def parse_patp(s):
    s = s.strip().lstrip('~').replace('--', '-')
    syl = s.split('-')
    if len(syl) == 1 and len(syl[0]) == 3:
        return fynd(SUF.index(syl[0]))
    v = 0
    for part in syl:
        v = (v << 16) | (PRE.index(part[:3]) << 8) | SUF.index(part[3:])
    return fynd(v)

def clan(p):
    if p < 0x100: return 'galaxy'
    if p < 0x10000: return 'star'
    if p < 0x100000000: return 'planet'
    if p < 0x10000000000000000: return 'moon'
    return 'comet'

if __name__ == '__main__':
    # self-check
    for n in [0, 1, 2**70, (1, 2), (0, ((1, 2), 3)), ((1, 1), (1, 1)), path('a', 'b', 'c')]:
        assert cue_bytes(jam_bytes(n)) == n, n
    assert patp(0) == '~zod' and patp(256) == '~marzod' and patp(0xffff) == '~fipfes'
    assert parse_patp(patp(1234567)) == 1234567
    for p in [65536, 1234567, 0xdeadbeef, 0x123456789abc, 2**64 + 5]:
        assert parse_patp(patp(p)) == p, (p, patp(p))
    print('ok', patp(0x10000), patp(0x123456789abc))

def show(n, depth=0, maxlen=40000):
    """debug pretty-printer: cords where printable, else numbers."""
    if isinstance(n, int):
        s = untas(n)
        if s is not None and s and all(32 <= ord(c) < 127 for c in s) and len(s) <= 40:
            return '%' + s
        return str(n)
    parts = []
    while isinstance(n, tuple):
        parts.append(show(n[0], depth + 1)); n = n[1]
    parts.append(show(n, depth + 1))
    return '[' + ' '.join(parts) + ']'
