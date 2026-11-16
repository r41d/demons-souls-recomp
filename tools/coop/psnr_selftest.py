"""Self-test for mini_psnr.py (stage 2). Starts the server as a subprocess on a free 127.0.0.1 port, plays two
(three) clients through HELLO, probes, CREATE/SEARCH/JOIN/SET_ROOM_DATA/ROOM_MESSAGE/KICK/LEAVE and a dropped
connection, and decodes every reply and push with the field layout our C client uses. C references are
ps3recomp libs/network/{psnr/psnr.c, sceNpMatching2.c, np_psnr.c}.   usage: psnr_selftest.py"""
import os, socket, struct, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
HELLO, HEARTBEAT = 0x01, 0x02
CREATE_ROOM, SEARCH_ROOMS, JOIN_ROOM, LEAVE_ROOM, SET_ROOM_DATA, ROOM_MESSAGE, KICK_MEMBER = range(0x10, 0x17)
HELLO_ACK, ROOM_JOINED, ROOM_LIST, OK, ERROR = 0x81, 0x90, 0x91, 0x93, 0x8F
MEMBER_JOINED, MEMBER_LEFT, ROOM_DATA, ROOM_MSG, KICKED = 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
E_NOT_FOUND, E_ROOM_FULL, E_NOT_OWNER, E_NOT_IN_ROOM, E_NO_HELLO, E_NAME_TAKEN = 2, 3, 4, 5, 7, 8
get16 = lambda b, o: struct.unpack_from('>H', b, o)[0]
get32 = lambda b, o: struct.unpack_from('>I', b, o)[0]
get64 = lambda b, o: struct.unpack_from('>Q', b, o)[0]
CHECKS = 0


def check(cond, what):
    global CHECKS
    if not cond:
        raise AssertionError(what)
    CHECKS += 1


# ---------------------------------------------------------------- C-layout decoders
def attrs_pack(attrs):
    """sceNpMatching2.c:298-309 attrs_pack: id u16 | kind u8 | (kind 0: num u32 | else len u16 + bytes)."""
    out = b''
    for aid, v in attrs:
        out += struct.pack('>HBI', aid, 0, v) if isinstance(v, int) else struct.pack('>HBH', aid, 1, len(v)) + v
    return out


def attrs_unpack(b):
    """sceNpMatching2.c:311-334 attrs_unpack (same stop conditions)."""
    out, p = [], 0
    while p + 3 <= len(b):
        aid, kind = get16(b, p), b[p + 2]
        p += 3
        if kind == 0:
            if p + 4 > len(b): break
            out.append((aid, get32(b, p))); p += 4
        else:
            if p + 2 > len(b): break
            n = get16(b, p); p += 2
            if n > 256 or p + n > len(b): break
            out.append((aid, b[p:p + n])); p += n
    return out


def parse_member(b, p):
    """sceNpMatching2.c:370-388 parse_member: id u16 @0 | user u32 @2 | online_id[16] @6 | ip[4] @22 | port u16 @26 |
    owner u8 @28 | data blob len u16 @29, data @31."""
    assert p + 31 <= len(b), 'member entry short'
    m = dict(id=get16(b, p), user=get32(b, p + 2), name=b[p + 6:p + 22].split(b'\0')[0].decode(),
             ip=socket.inet_ntoa(b[p + 22:p + 26]), port=get16(b, p + 26), owner=b[p + 28])
    dl = get16(b, p + 29)
    p += 31
    assert p + dl <= len(b), 'member data overruns'
    m['data'] = attrs_unpack(b[p:p + dl])
    return m, p + dl


def parse_joined(b):
    """sceNpMatching2.c:390-414 parse_joined: room u64 @0 | me u16 @8 | owner u16 @10 | max u8 @12 | flags u32 @13 |
    ext blob @17 | int blob | n u8 | members."""
    assert len(b) >= 17
    r = dict(id=get64(b, 0), me=get16(b, 8), owner=get16(b, 10), max=b[12], flags=get32(b, 13))
    p = 17
    l = get16(b, p); p += 2; assert p + l <= len(b); r['ext'] = attrs_unpack(b[p:p + l]); p += l
    l = get16(b, p); p += 2; assert p + l <= len(b); r['int'] = attrs_unpack(b[p:p + l]); p += l
    assert p < len(b)
    n = b[p]; p += 1
    r['members'] = []
    for _ in range(n):
        m, p = parse_member(b, p)
        r['members'].append(m)
    assert p == len(b), 'trailing bytes in ROOM_JOINED'
    return r


def parse_room_list(b):
    """sceNpMatching2.c:966-988 on_search: total u16 @0 | count u16 @2 | entries: room u64 @0 | owner[16] @8 |
    cur u8 @24 | max u8 @25 | flags u32 @26 | ext len u16 @30 | ext @32."""
    total, count, p, rooms = get16(b, 0), get16(b, 2), 4, []
    i = 0
    while i < count and p + 34 <= len(b):        # :982, the same loop test as the C (34, not 32)
        l = get16(b, p + 30)
        ext_p = p + 32
        if p + 32 + l > len(b):                   # :990-991 p > end -> break
            break
        rooms.append(dict(id=get64(b, p), owner=b[p + 8:p + 24].split(b'\0')[0].decode(), cur=b[p + 24], max=b[p + 25],
                          flags=get32(b, p + 26), ext=attrs_unpack(b[ext_p:ext_p + l])))
        p += 32 + l
        i += 1
    assert len(rooms) == count, 'client would list %d of %d rooms' % (len(rooms), count)
    assert len(b) - p == 2, 'ROOM_LIST should end with the 2 pad bytes'
    return total, rooms


def parse_push(t, b):
    """sceNpMatching2.c:635-722 on_push: room u64 first, then per type."""
    room, p = get64(b, 0), 8
    if t == MEMBER_JOINED:                       # :647 parse_member at data+8
        m, end = parse_member(b, p); assert end == len(b); return room, m
    if t == MEMBER_LEFT:                         # :657-659 gone u16 | owner u16
        assert len(b) - p >= 4; return room, dict(gone=get16(b, p), owner=get16(b, p + 2))
    if t == ROOM_DATA:                           # :679-687 which u8 | len u16 | data
        which, l = b[p], get16(b, p + 1); assert p + 3 + l == len(b)
        return room, dict(which=which, data=b[p + 3:p + 3 + l])
    if t == ROOM_MSG:                            # :691-694 from u16 | to u16 | len u16 | msg
        frm, to, l = get16(b, p), get16(b, p + 2), get16(b, p + 4); assert p + 6 + l == len(b)
        return room, dict(frm=frm, to=to, msg=b[p + 6:p + 6 + l])
    if t == KICKED:                              # :716 room only
        return room, {}
    raise AssertionError('unknown push %x' % t)


# ---------------------------------------------------------------- a client with psnr.c framing
class Conn:
    def __init__(self, port):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.rx, self.q, self.next_req = b'', [], 0

    def send(self, typ, body=b''):
        """psnr.c:94-104 psnr_send: type u8 | len u16 (= body + 4) | req u32 | body."""
        self.next_req += 1
        self.s.sendall(struct.pack('>BHI', typ, len(body) + 4, self.next_req) + body)
        return self.next_req

    def pump(self, timeout):
        self.s.settimeout(timeout)
        try:
            k = self.s.recv(65536)
        except socket.timeout:
            return
        if not k:
            raise EOFError
        self.rx += k
        while len(self.rx) >= 3:   # psnr.c:121-137 framing; replies carry req, pushes (0xAx) don't
            t, n = self.rx[0], get16(self.rx, 1)
            if len(self.rx) < 3 + n: break
            p, self.rx = self.rx[3:3 + n], self.rx[3 + n:]
            req = 0
            if (t & 0xF0) != 0xA0 and n >= 4:
                req, p = get32(p, 0), p[4:]
            self.q.append((t, req, p))

    def wait(self, pred, what, timeout=3.0):
        end = time.time() + timeout
        while True:
            for i, m in enumerate(self.q):
                if pred(m):
                    return self.q.pop(i)
            if time.time() > end:
                raise AssertionError('timeout waiting for ' + what + '; queue ' + repr(self.q))
            self.pump(0.1)

    def call(self, typ, body=b''):
        req = self.send(typ, body)
        t, _, p = self.wait(lambda m: m[1] == req, 'reply to %x' % typ)
        return t, p

    def push(self, typ):
        t, _, p = self.wait(lambda m: m[1] == 0 and m[0] == typ, 'push %x' % typ)
        return parse_push(t, p)

    def no_push(self, wait=0.3):
        self.pump(wait)
        check(not [m for m in self.q if m[1] == 0], 'unexpected push %r' % self.q)

    def hello(self, name, p2p_port, comm='NPWR00860_00'):
        """psnr.c:243-247 HELLO body; HELLO_ACK read as psnr.c:254-260."""
        body = comm.encode().ljust(12, b'\0')[:12] + name.encode().ljust(16, b'\0')[:16] + struct.pack('>H', p2p_port)
        t, p = self.call(HELLO, body)
        if t == ERROR:
            return None, get32(p, 0)
        check(t == HELLO_ACK and len(p) >= 8, 'HELLO_ACK')
        self.user, self.ip, self.token = get32(p, 0), socket.inet_ntoa(p[4:8]), get32(p, 8)
        check(len(p) >= 13 and (p[12] & 1) == 0, 'relay flag off')
        return self.user, 0


def err(t, p):
    """sceNpMatching2.c:941-948 psnr_error_to_m2: ERROR body = code u32."""
    return get32(p, 0) if t == ERROR and len(p) >= 4 else None


def probe(udp, conn, port):
    """psnr.c:272-279 probe "PSNR" 01 | user | token | local_ip; reply psnr.c:293-300 "PSNR" 81 | ip | port."""
    udp.sendto(b'PSNR\x01' + struct.pack('>II', conn.user, conn.token) + socket.inet_aton('127.0.0.1'), ('127.0.0.1', port))
    d, _ = udp.recvfrom(64)
    check(len(d) >= 11 and d[:5] == b'PSNR\x81', 'probe reply')
    return socket.inet_ntoa(d[5:9]), get16(d, 9)


def free_port():
    while True:
        t = socket.socket(); t.bind(('127.0.0.1', 0)); port = t.getsockname()[1]
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            u.bind(('127.0.0.1', port)); return port
        except OSError:
            pass
        finally:
            t.close(); u.close()


def main():
    port = free_port()
    logf = os.path.join(tempfile.gettempdir() if not os.path.isdir(HERE) else HERE, 'psnr_selftest_server.log')
    open(logf, 'w').close()
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, 'mini_psnr.py'), logf, '--port', str(port)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = srv.stdout.readline()
        check('listening' in line, 'server start: ' + line)
        run(port)
        print('PASS: %d checks; server log %s' % (CHECKS, logf))
    finally:
        srv.kill(); srv.wait()


def run(port):
    # P2P sockets the "games" own, on distinct ports (as PS3_NET_P2P_PORT must be per copy on one PC)
    ua = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); ua.bind(('127.0.0.1', 0)); ua.settimeout(3)
    ub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); ub.bind(('127.0.0.1', 0)); ub.settimeout(3)
    pa, pb = ua.getsockname()[1], ub.getsockname()[1]

    # no HELLO yet -> NO_HELLO
    x = Conn(port)
    check(err(*x.call(CREATE_ROOM, b'\x02' + bytes(4) + bytes(6))) == E_NO_HELLO, 'NO_HELLO')
    x.s.close()

    A, B = Conn(port), Conn(port)
    ua_id, _ = A.hello('alice', pa)
    ub_id, _ = B.hello('bob', pb)
    check(ua_id != ub_id and A.ip == '127.0.0.1', 'HELLO_ACK fields')
    print('HELLO   alice user %d, bob user %d, seen from %s' % (ua_id, ub_id, A.ip))
    D = Conn(port)
    check(D.hello('alice', 4000)[1] == E_NAME_TAKEN, 'NAME_TAKEN')
    D.s.close()
    check(A.call(HEARTBEAT) == (OK, b''), 'HEARTBEAT -> OK')

    check(probe(ua, A, port) == ('127.0.0.1', pa), 'alice probe endpoint')
    check(probe(ub, B, port) == ('127.0.0.1', pb), 'bob probe endpoint')
    print('PROBE   alice P2P 127.0.0.1:%d, bob P2P 127.0.0.1:%d' % (pa, pb))

    # CREATE_ROOM: max u8 | flags u32 | ext | int | member data (sceNpMatching2.c:1133-1140)
    ext = [(0x4C, 7), (0x54, b'world-1-1'), (0x55, b'\x01\x02')]
    internal = [(0x57, b'slots')]
    mine_a = [(0x59, b'alice-chr')]
    body = struct.pack('>BI', 4, 0x10) + b''.join(struct.pack('>H', len(x)) + x for x in
                                                 (attrs_pack(ext), attrs_pack(internal), attrs_pack(mine_a)))
    t, p = A.call(CREATE_ROOM, body)
    check(t == ROOM_JOINED, 'CREATE_ROOM -> ROOM_JOINED')
    r = parse_joined(p)
    rid = r['id']
    check(rid > 0xFFFFFFFF and r['me'] == 1 and r['owner'] == 1 and r['max'] == 4 and r['flags'] == 0x10, 'joined header')
    check(r['ext'] == ext and r['int'] == internal and len(r['members']) == 1, 'joined attrs')
    m = r['members'][0]
    check((m['id'], m['user'], m['name'], m['ip'], m['port'], m['owner'], m['data']) ==
          (1, ua_id, 'alice', '127.0.0.1', pa, 1, mine_a), 'creator member entry')
    print('CREATE  room %#x, alice member 1 (owner) at %s:%d' % (rid, m['ip'], m['port']))

    # SEARCH_ROOMS: start u16 | max u16 (sceNpMatching2.c:1080-1083)
    t, p = B.call(SEARCH_ROOMS, struct.pack('>HH', 0, 100))
    check(t == ROOM_LIST, 'ROOM_LIST')
    total, rooms = parse_room_list(p)
    check(total == 1 and rooms[0] == dict(id=rid, owner='alice', cur=1, max=4, flags=0x10, ext=ext), 'room list entry')
    print('SEARCH  bob sees %r' % rooms)

    # JOIN_ROOM: room u64 | member data (sceNpMatching2.c:1157-1158)
    mine_b = [(0x59, b'bob-chr')]
    jb = struct.pack('>Q', rid) + struct.pack('>H', len(attrs_pack(mine_b))) + attrs_pack(mine_b)
    t, p = B.call(JOIN_ROOM, jb)
    check(t == ROOM_JOINED, 'JOIN_ROOM -> ROOM_JOINED')
    r = parse_joined(p)
    check(r['id'] == rid and r['me'] == 2 and r['owner'] == 1 and len(r['members']) == 2, 'join header')
    ms = {m['id']: m for m in r['members']}
    check((ms[1]['name'], ms[1]['ip'], ms[1]['port'], ms[1]['owner'], ms[1]['data']) == ('alice', '127.0.0.1', pa, 1, mine_a), 'bob sees alice endpoint')
    check((ms[2]['name'], ms[2]['ip'], ms[2]['port'], ms[2]['owner'], ms[2]['data']) == ('bob', '127.0.0.1', pb, 0, mine_b), 'bob sees himself')
    room, m = A.push(MEMBER_JOINED)
    check(room == rid and (m['id'], m['user'], m['name'], m['ip'], m['port'], m['data']) == (2, ub_id, 'bob', '127.0.0.1', pb, mine_b), 'MEMBER_JOINED')
    print('JOIN    bob member 2; bob got alice at 127.0.0.1:%d, alice got MEMBER_JOINED bob at %s:%d' % (pa, m['ip'], m['port']))

    # errors: NOT_FOUND, ROOM_FULL
    C = Conn(port); C.hello('carol', 4002)
    check(err(*C.call(JOIN_ROOM, struct.pack('>QH', rid + 99, 0))) == E_NOT_FOUND, 'JOIN unknown -> NOT_FOUND')
    t, p = C.call(CREATE_ROOM, struct.pack('>BI', 1, 0) + bytes(6))
    small = parse_joined(p)['id']
    # the last room in a list with an empty ext blob: on_search's p + 34 test would drop it without the server's pad
    total, rooms = parse_room_list(B.call(SEARCH_ROOMS, struct.pack('>HH', 0, 100))[1])
    check(total == 2 and [r['id'] for r in rooms] == [rid, small] and rooms[1]['ext'] == [] and rooms[1]['owner'] == 'carol',
          'empty-ext room listed last')
    t, p = A.call(JOIN_ROOM, struct.pack('>QH', small, 0))
    check(err(t, p) == E_ROOM_FULL, 'JOIN full -> ROOM_FULL')
    check(C.call(LEAVE_ROOM, struct.pack('>Q', small))[0] == OK, 'carol leaves her room')
    check(err(*C.call(LEAVE_ROOM, struct.pack('>Q', rid))) == E_NOT_IN_ROOM, 'LEAVE not member -> NOT_IN_ROOM')
    print('ERRORS  NOT_FOUND, ROOM_FULL, NOT_IN_ROOM ok')

    # SET_ROOM_DATA: room u64 | which u8 | len u16 | data (sceNpMatching2.c:1227-1229, 1248-1257)
    newint = attrs_pack([(0x57, b'slots2')])
    check(A.call(SET_ROOM_DATA, struct.pack('>QBH', rid, 1, len(newint)) + newint) == (OK, b''), 'SET internal')
    room, d = B.push(ROOM_DATA)
    check(room == rid and d['which'] == 1 and attrs_unpack(d['data']) == [(0x57, b'slots2')], 'ROOM_DATA internal push')
    check(A.call(SET_ROOM_DATA, struct.pack('>QBHI', rid, 2, 4, 0x30)) == (OK, b''), 'SET flags')
    room, d = B.push(ROOM_DATA)
    check(d['which'] == 2 and get32(d['data'], 0) == 0x30, 'ROOM_DATA flags push')
    newext = attrs_pack([(0x4C, 9)])
    check(A.call(SET_ROOM_DATA, struct.pack('>QBH', rid, 0, len(newext)) + newext) == (OK, b''), 'SET external')
    room, d = B.push(ROOM_DATA)
    check(d['which'] == 0, 'ROOM_DATA external push')
    A.no_push()   # the writer gets no push of its own write
    _, rooms = parse_room_list(B.call(SEARCH_ROOMS, struct.pack('>HH', 0, 100))[1])
    check(rooms[0]['flags'] == 0x30 and rooms[0]['ext'] == [(0x4C, 9)], 'search sees new flags/ext')
    print('DATA    internal/flags/external stored and pushed to bob')

    # ROOM_MESSAGE: room u64 | to u16 | len u16 | msg (sceNpMatching2.c:1293-1296)
    msg = b'\x00\x01hello alice'
    check(B.call(ROOM_MESSAGE, struct.pack('>QHH', rid, 1, len(msg)) + msg) == (OK, b''), 'unicast')
    room, d = A.push(ROOM_MSG)
    check(room == rid and d == dict(frm=2, to=1, msg=msg), 'ROOM_MSG unicast')
    check(A.call(ROOM_MESSAGE, struct.pack('>QHH', rid, 0, 3) + b'all') == (OK, b''), 'broadcast')
    room, d = B.push(ROOM_MSG)
    check(d == dict(frm=1, to=0, msg=b'all'), 'ROOM_MSG broadcast')
    A.no_push()
    print('MSG     unicast bob->alice and broadcast alice->bob relayed')

    # KICK_MEMBER: room u64 | target u16 (sceNpMatching2.c:1213); only the owner
    check(err(*B.call(KICK_MEMBER, struct.pack('>QH', rid, 1))) == E_NOT_OWNER, 'non-owner kick -> NOT_OWNER')

    # LEAVE_ROOM: room u64 (sceNpMatching2.c:1203)
    check(B.call(LEAVE_ROOM, struct.pack('>Q', rid)) == (OK, b''), 'bob leaves')
    check(A.push(MEMBER_LEFT) == (rid, dict(gone=2, owner=1)), 'MEMBER_LEFT bob')
    t, p = B.call(JOIN_ROOM, jb); check(parse_joined(p)['me'] == 3, 'bob rejoins as 3')
    A.push(MEMBER_JOINED)
    check(A.call(LEAVE_ROOM, struct.pack('>Q', rid)) == (OK, b''), 'alice (owner) leaves')
    check(B.push(MEMBER_LEFT) == (rid, dict(gone=1, owner=3)), 'ownership passes to bob')
    print('LEAVE   bob left (alice told), alice left, ownership -> bob')

    t, p = A.call(JOIN_ROOM, struct.pack('>QH', rid, 0))
    r = parse_joined(p); check(r['me'] == 4 and r['owner'] == 3, 'alice rejoins as 4, bob owner')
    B.push(MEMBER_JOINED)
    check(B.call(KICK_MEMBER, struct.pack('>QH', rid, 4)) == (OK, b''), 'owner kicks')
    check(A.push(KICKED) == (rid, {}), 'KICKED')
    check(B.push(MEMBER_LEFT) == (rid, dict(gone=4, owner=3)), 'MEMBER_LEFT after kick')
    print('KICK    bob kicked alice: KICKED to alice, MEMBER_LEFT to bob')

    # a dropped TCP connection leaves its rooms
    t, p = A.call(JOIN_ROOM, struct.pack('>QH', rid, 0)); B.push(MEMBER_JOINED)
    B.s.close()
    check(A.push(MEMBER_LEFT) == (rid, dict(gone=3, owner=5)), 'disconnect -> MEMBER_LEFT, owner alice')
    check(A.call(LEAVE_ROOM, struct.pack('>Q', rid)) == (OK, b''), 'alice leaves last')
    total, rooms = parse_room_list(C.call(SEARCH_ROOMS, struct.pack('>HH', 0, 100))[1])
    check(total == 0 and rooms == [], 'empty rooms deleted')
    print('DROP    bob disconnected -> alice told, owner alice; empty room deleted')


if __name__ == '__main__':
    main()
