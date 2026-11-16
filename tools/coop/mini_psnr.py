"""A minimal psnr-protocol test server (our own), listening on 127.0.0.1 only.

Stage 2: HELLO, heartbeats, UDP probes, and rooms (create / search / join / leave / kick / room data / room
messages), in the byte layouts our client (ps3recomp libs/network: psnr/psnr.c, np_psnr.c, sceNpMatching2.c)
builds and parses. Scores and the relay are not served: those requests are logged and refused.

usage: mini_psnr.py <logfile> [--port N]      (default port 36100; TCP and UDP on the same port)

Wire format (psnr.c:94-104, 121-137): every frame is  type u8 | len u16 | payload[len], big-endian.
Requests and replies (0x0x-0x3x, 0x8x, 0x9x, 0xBx) start the payload with the request id u32; pushes (0xAx) don't.
"""
import selectors, socket, struct, sys, time

HOST = '127.0.0.1'
IDLE_DROP_SECS = 90          # psnr.h: a client that stops polling for 90 s is dropped (heartbeat every 20 s)
ROOM_ID_BASE = 0x0000000100000000   # room ids above 2^32, so the client's u64 path is exercised

# opcodes (psnr.h)
HELLO, HEARTBEAT = 0x01, 0x02
CREATE_ROOM, SEARCH_ROOMS, JOIN_ROOM, LEAVE_ROOM, SET_ROOM_DATA, ROOM_MESSAGE, KICK_MEMBER = range(0x10, 0x17)
RECORD_SCORE, GET_RANKING, GET_RANKING_BY_ID = 0x20, 0x21, 0x22
STREAM_CONNECT, STREAM_ACCEPT = 0x30, 0x31
HELLO_ACK, ROOM_JOINED, ROOM_LIST, OK, ERROR = 0x81, 0x90, 0x91, 0x93, 0x8F
MEMBER_JOINED, MEMBER_LEFT, ROOM_DATA, ROOM_MSG, KICKED = 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
E_BAD_REQUEST, E_NOT_FOUND, E_ROOM_FULL, E_NOT_OWNER, E_NOT_IN_ROOM, E_LIMIT, E_NO_HELLO, E_NAME_TAKEN, E_NO_RELAY = range(1, 10)

NAMES = {0x01: 'HELLO', 0x02: 'HEARTBEAT', 0x10: 'CREATE_ROOM', 0x11: 'SEARCH_ROOMS', 0x12: 'JOIN_ROOM', 0x13: 'LEAVE_ROOM',
         0x14: 'SET_ROOM_DATA', 0x15: 'ROOM_MESSAGE', 0x16: 'KICK_MEMBER', 0x20: 'RECORD_SCORE', 0x21: 'GET_RANKING',
         0x22: 'GET_RANKING_BY_ID', 0x30: 'STREAM_CONNECT', 0x31: 'STREAM_ACCEPT',
         0x81: 'HELLO_ACK', 0x90: 'ROOM_JOINED', 0x91: 'ROOM_LIST', 0x93: 'OK', 0x8F: 'ERROR', 0x96: 'STREAM_READY',
         0xA1: 'MEMBER_JOINED', 0xA2: 'MEMBER_LEFT', 0xA3: 'ROOM_DATA', 0xA4: 'ROOM_MSG', 0xA5: 'KICKED',
         0xA6: 'STREAM_OFFER', 0xA7: 'ROUTE'}
ENAMES = {1: 'BAD_REQUEST', 2: 'NOT_FOUND', 3: 'ROOM_FULL', 4: 'NOT_OWNER', 5: 'NOT_IN_ROOM', 6: 'LIMIT', 7: 'NO_HELLO',
          8: 'NAME_TAKEN', 9: 'NO_RELAY'}
MEMBER_MAX = 16              # sceNpMatching2.c:133 -- the client keeps at most 16 members per room

LOG = None
T0 = time.time()


def log(*a):
    print(time.strftime("%H:%M:%S"), "%9.3f" % (time.time() - T0), *a, file=LOG)


def hx(b, n=48):
    return '%d:%s%s' % (len(b), b[:n].hex(), '..' if len(b) > n else '')


class Bad(Exception):
    """A request we refuse: carries the psnr ERROR code."""


def blob(body, off):
    """u16 length + bytes (the attrs blobs: sceNpMatching2.c:1138-1140)."""
    if off + 2 > len(body):
        raise Bad(E_BAD_REQUEST)
    n = struct.unpack_from('>H', body, off)[0]
    if off + 2 + n > len(body):
        raise Bad(E_BAD_REQUEST)
    return body[off + 2:off + 2 + n], off + 2 + n


def pblob(b):
    return struct.pack('>H', len(b)) + b


def name16(s):
    return s.encode('utf-8', 'replace')[:16].ljust(16, b'\0')


class Client:
    next_id = 1

    def __init__(self, srv, s, addr):
        self.srv, self.s, self.addr = srv, s, addr
        self.rx = b''
        self.id = self.token = 0
        self.name, self.comm, self.p2p_port = '?', '', 0
        self.udp_ep = None            # (ip, port) the server saw this client's P2P socket probe from
        self.last_rx = time.time()
        self.dead = False

    def label(self):
        return '%s#%d' % (self.name, self.id) if self.id else '%s:%d' % self.addr

    def endpoint(self):
        """The P2P address peers get in member entries (parse_member: ip @22, port @26). The probe-observed
        endpoint when we have one (np_psnr.c nat_tick probes from the P2P socket), else the TCP peer's IP
        with the port HELLO declared (PS3_NET_P2P_PORT, which sysNet.c:398 binds the P2P socket to)."""
        return self.udp_ep or (self.addr[0], self.p2p_port)

    def send(self, typ, body, req=None):
        p = (struct.pack('>I', req) if req is not None else b'') + body
        if len(p) > 0xFFFF:
            log('!! frame too big for', self.label(), NAMES.get(typ, hex(typ)), len(p))
            if req is None:
                return
            # a reply must still come back: np_psnr.c keeps the request pending (and the title waiting) until it does
            p = struct.pack('>II', req, E_LIMIT)
            typ = ERROR
        try:
            self.s.sendall(struct.pack('>BH', typ, len(p)) + p)
        except OSError as e:
            log('!! send to', self.label(), 'failed:', e)
            self.dead = True

    def reply(self, typ, req, body=b''):
        extra = (' ' + ENAMES.get(struct.unpack('>I', body[:4])[0], '?')) if typ == ERROR and len(body) >= 4 else ''
        log('  reply ->', self.label(), NAMES.get(typ, hex(typ)) + extra, 'req', req, hx(body))
        self.send(typ, body, req)

    def push(self, typ, body):
        log('  push  ->', self.label(), NAMES.get(typ, hex(typ)), hx(body))
        self.send(typ, body)


class Member:
    def __init__(self, mid, client, data):
        self.mid, self.client, self.data = mid, client, data


class Room:
    def __init__(self, rid, comm, max_, flags, ext, internal):
        self.id, self.comm, self.max, self.flags, self.ext, self.internal = rid, comm, max_, flags, ext, internal
        self.members = {}             # member id -> Member, in join order
        self.owner = 0
        self.next_mid = 1

    def member_of(self, c):
        for m in self.members.values():
            if m.client is c:
                return m
        return None

    def entry(self, m):
        """member entry: id u16 | user u32 | online_id [16] | ip [4] | port u16 | owner u8 | data blob
        (sceNpMatching2.c:370-388 parse_member)."""
        ip, port = m.client.endpoint()
        return (struct.pack('>HI', m.mid, m.client.id) + name16(m.client.name) + socket.inet_aton(ip) +
                struct.pack('>HB', port, 1 if m.mid == self.owner else 0) + pblob(m.data))

    def joined(self, me):
        """ROOM_JOINED: room u64 | me u16 | owner u16 | max u8 | flags u32 | ext blob | int blob | n u8 | members
        (sceNpMatching2.c:390-414 parse_joined)."""
        ms = list(self.members.values())
        return (struct.pack('>QHHBI', self.id, me, self.owner, self.max, self.flags) + pblob(self.ext) +
                pblob(self.internal) + bytes([len(ms)]) + b''.join(self.entry(m) for m in ms))

    def others(self, c):
        return [m for m in self.members.values() if m.client is not c]


class Server:
    def __init__(self, port):
        self.port = port
        self.clients = {}             # socket -> Client
        self.rooms = {}               # room id -> Room
        self.next_room = 1
        self.sel = selectors.DefaultSelector()
        self.tcp = socket.socket()
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind((HOST, port))
        self.tcp.listen()
        self.tcp.setblocking(False)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind((HOST, port))
        self.udp.setblocking(False)
        self.sel.register(self.tcp, selectors.EVENT_READ, 'accept')
        self.sel.register(self.udp, selectors.EVENT_READ, 'udp')
        log('listening', HOST, port, '(tcp+udp), stage 2: rooms')

    # ---- plumbing ----
    def run(self):
        last_sweep = time.time()
        while True:
            for key, _ in self.sel.select(1.0):
                if key.data == 'accept':
                    s, addr = self.tcp.accept()
                    s.setblocking(True)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    c = Client(self, s, addr)
                    self.clients[s] = c
                    self.sel.register(s, selectors.EVENT_READ, c)
                    log('connect', addr)
                elif key.data == 'udp':
                    try:
                        d, a = self.udp.recvfrom(4096)
                    except OSError:
                        continue
                    self.on_udp(d, a)
                else:
                    self.on_tcp(key.data)
            for c in [c for c in self.clients.values() if c.dead]:
                self.drop(c, 'send failed')
            now = time.time()
            if now - last_sweep >= 1:
                last_sweep = now
                for c in [c for c in self.clients.values() if now - c.last_rx > IDLE_DROP_SECS]:
                    self.drop(c, 'silent for %d s' % IDLE_DROP_SECS)

    def on_tcp(self, c):
        try:
            k = c.s.recv(65536)
        except OSError:
            k = b''
        if not k:
            self.drop(c, 'connection closed')
            return
        c.last_rx = time.time()
        c.rx += k
        while len(c.rx) >= 3 and c.s in self.clients:
            typ, n = struct.unpack('>BH', c.rx[:3])
            if len(c.rx) < 3 + n:
                break
            p, c.rx = c.rx[3:3 + n], c.rx[3 + n:]
            if n < 4:
                log('REQ ', c.label(), NAMES.get(typ, hex(typ)), 'short frame', hx(p))
                continue
            req, body = struct.unpack('>I', p[:4])[0], p[4:]
            if typ != HEARTBEAT:
                log('REQ ', c.label(), NAMES.get(typ, hex(typ)), 'req', req, hx(body))
            try:
                self.handle(c, typ, req, body)
            except Bad as e:
                c.reply(ERROR, req, struct.pack('>I', e.args[0]))
            except (struct.error, IndexError, UnicodeError) as e:
                log('!! malformed', NAMES.get(typ, hex(typ)), 'from', c.label(), e)
                c.reply(ERROR, req, struct.pack('>I', E_BAD_REQUEST))

    def drop(self, c, why):
        if c.s not in self.clients:
            return
        log('gone', c.label(), '(%s)' % why)
        for r in list(self.rooms.values()):
            m = r.member_of(c)
            if m:
                self.remove_member(r, m, kicked=False)
        del self.clients[c.s]
        try:
            self.sel.unregister(c.s)
        except (KeyError, ValueError):
            pass
        try:
            c.s.close()
        except OSError:
            pass

    # ---- UDP: probes (psnr.c:272-279 / 293-300) ----
    def on_udp(self, d, a):
        if d[:4] == b'PSNR' and len(d) >= 5 and d[4] == 0x01 and len(d) >= 17:
            # "PSNR" 01 | user u32 | token u32 | local_ip [4]  ->  "PSNR" 81 | ip [4] | port u16
            user, token = struct.unpack_from('>II', d, 5)
            c = next((c for c in self.clients.values() if c.id == user), None)
            if c and c.token == token:
                if c.udp_ep != a:
                    in_rooms = [hex(r.id) for r in self.rooms.values() if r.member_of(c)]
                    log('udp probe:', c.label(), 'P2P socket at %s:%d' % a, '(hello said port %d)' % c.p2p_port,
                        ('-- already in rooms %s; their members keep the old endpoint' % in_rooms) if in_rooms and a != c.endpoint() else '')
                    c.udp_ep = a
                self.udp.sendto(b'PSNR\x81' + socket.inet_aton(a[0]) + struct.pack('>H', a[1]), a)
            else:
                log('udp probe from %s:%d with unknown user %d / bad token -- ignored' % (a[0], a[1], user))
        elif d[:4] == b'PSNR' and len(d) >= 5 and d[4] == 0x03:
            log('udp relay datagram from %s:%d -- no relay here (HELLO_ACK says so), dropped' % a, hx(d, 32))
        else:
            log('udp ?? from %s:%d' % a, hx(d, 32))

    # ---- requests ----
    def need_hello(self, c):
        if not c.id:
            raise Bad(E_NO_HELLO)

    def room_of(self, c, rid):
        r = self.rooms.get(rid)
        if not r:
            raise Bad(E_NOT_FOUND)
        m = r.member_of(c)
        if not m:
            raise Bad(E_NOT_IN_ROOM)
        return r, m

    def handle(self, c, typ, req, body):
        if typ == HELLO:
            # HELLO: comm_id [12] | online_id [16] | p2p_port u16 (psnr.c:243-247)
            # HELLO_ACK: user u32 | ip [4] | token u32 | flags u8 (bit 0 = relay) (psnr.c:254-260)
            if len(body) < 30:
                raise Bad(E_BAD_REQUEST)
            comm = body[:12].rstrip(b'\0').decode('ascii', 'replace')
            name = body[12:28].split(b'\0')[0].decode('utf-8', 'replace')
            port = struct.unpack_from('>H', body, 28)[0]
            if any(o is not c and o.id and o.name == name for o in self.clients.values()):
                log('HELLO', comm, repr(name), '-- name taken')
                raise Bad(E_NAME_TAKEN)
            if not c.id:
                c.id, Client.next_id = Client.next_id, Client.next_id + 1
                c.token = (0x5A5A0000 ^ (c.id * 0x9E3779B1)) & 0xFFFFFFFF
            c.name, c.comm, c.p2p_port = name, comm, port
            log('HELLO', comm, repr(name), 'p2p', port, 'from %s:%d' % c.addr, '-> user', c.id)
            same = [o.label() for o in self.clients.values()
                    if o is not c and o.id and o.addr[0] == c.addr[0] and o.p2p_port == port]
            if same:
                log('!! WARNING:', c.label(), 'declares the same P2P port', port, 'as', same,
                    'on the same IP -- set a different PS3_NET_P2P_PORT per copy')
            c.reply(HELLO_ACK, req, struct.pack('>I', c.id) + socket.inet_aton(c.addr[0]) +
                    struct.pack('>IB', c.token, 0))
        elif typ == HEARTBEAT:
            c.send(OK, b'', req)
        elif typ == CREATE_ROOM:
            self.need_hello(c)
            # max u8 | flags u32 | ext blob | int blob | member data blob (sceNpMatching2.c:1133-1140)
            max_, flags = struct.unpack_from('>BI', body, 0)
            ext, o = blob(body, 5)
            internal, o = blob(body, o)
            mine, o = blob(body, o)
            max_ = max(1, min(max_ or MEMBER_MAX, MEMBER_MAX))
            self.warn_other_rooms(c, None)
            rid = ROOM_ID_BASE + self.next_room
            self.next_room += 1
            r = Room(rid, c.comm, max_, flags, ext, internal)
            self.rooms[rid] = r
            m = Member(r.next_mid, c, mine)
            r.next_mid += 1
            r.members[m.mid] = m
            r.owner = m.mid
            log('room', hex(rid), 'created by', c.label(), 'max', max_, 'flags %08x' % flags,
                'ext', len(ext), 'int', len(internal), 'member data', len(mine))
            c.reply(ROOM_JOINED, req, r.joined(m.mid))
        elif typ == SEARCH_ROOMS:
            self.need_hello(c)
            # start u16 | max u16 (sceNpMatching2.c:1080-1083)
            start, want = struct.unpack_from('>HH', body, 0)
            rooms = [r for r in self.rooms.values() if r.comm == c.comm]
            page = rooms[start:start + want]
            # ROOM_LIST: total u16 | count u16 | (room u64 | owner [16] | cur u8 | max u8 | flags u32 | ext blob)...
            # (sceNpMatching2.c:966-988 on_search)
            entries = []
            size = 4 + 4 + 2                    # req + total/count + the pad below
            for r in page:
                om = r.members.get(r.owner)
                e = (struct.pack('>Q', r.id) + name16(om.client.name if om else '') +
                     struct.pack('>BBI', len(r.members), r.max, r.flags) + pblob(r.ext))
                if size + len(e) > 0xFFFF:      # one frame: list what fits
                    break
                size += len(e)
                entries.append(e)
            # on_search (sceNpMatching2.c:982) only reads an entry while p + 34 <= end, but an entry is 32 bytes
            # plus its ext blob: a last room with an ext blob under 2 bytes would be skipped. Two zero bytes after
            # the last entry keep it in; the client stops at `count` and never reads them.
            c.reply(ROOM_LIST, req, struct.pack('>HH', len(rooms), len(entries)) + b''.join(entries) + bytes(2))
        elif typ == JOIN_ROOM:
            self.need_hello(c)
            # room u64 | member data blob (sceNpMatching2.c:1157-1158)
            rid = struct.unpack_from('>Q', body, 0)[0]
            mine, _ = blob(body, 8)
            r = self.rooms.get(rid)
            if not r or r.comm != c.comm:
                raise Bad(E_NOT_FOUND)
            m = r.member_of(c)
            if m:
                log('room', hex(rid), c.label(), 'joins again; already member', m.mid)
                c.reply(ROOM_JOINED, req, r.joined(m.mid))
                return
            if len(r.members) >= r.max:
                raise Bad(E_ROOM_FULL)
            self.warn_other_rooms(c, rid)
            m = Member(r.next_mid, c, mine)
            r.next_mid += 1
            ip, port = c.endpoint()
            for o in r.others(c):
                if o.client.endpoint() == (ip, port):
                    log('!! WARNING: room', hex(rid), c.label(), 'and', o.client.label(), 'share P2P endpoint %s:%d' % (ip, port))
            r.members[m.mid] = m
            log('room', hex(rid), c.label(), 'joined as member', m.mid, 'p2p %s:%d' % (ip, port))
            c.reply(ROOM_JOINED, req, r.joined(m.mid))
            # MEMBER_JOINED: room u64 | member entry (sceNpMatching2.c:647-655)
            for o in r.others(c):
                o.client.push(MEMBER_JOINED, struct.pack('>Q', r.id) + r.entry(m))
        elif typ == LEAVE_ROOM:
            self.need_hello(c)
            # room u64 (sceNpMatching2.c:1203)
            r, m = self.room_of(c, struct.unpack_from('>Q', body, 0)[0])
            c.reply(OK, req)
            self.remove_member(r, m, kicked=False)
        elif typ == KICK_MEMBER:
            self.need_hello(c)
            # room u64 | target member u16 (sceNpMatching2.c:1213)
            rid, target = struct.unpack_from('>QH', body, 0)
            r, me = self.room_of(c, rid)
            if me.mid != r.owner:
                raise Bad(E_NOT_OWNER)
            t = r.members.get(target)
            if not t:
                raise Bad(E_NOT_FOUND)
            if t is me:
                raise Bad(E_BAD_REQUEST)
            c.reply(OK, req)
            self.remove_member(r, t, kicked=True)
        elif typ == SET_ROOM_DATA:
            self.need_hello(c)
            # room u64 | which u8 (0 ext, 1 internal attrs, 2 flags) | len u16 | data
            # (sceNpMatching2.c:1227-1229, 1248-1251, 1255-1257). The client sends the whole merged set each time.
            rid, which = struct.unpack_from('>QB', body, 0)
            data, _ = blob(body, 9)
            r, m = self.room_of(c, rid)
            if which == 0:
                r.ext = data
            elif which == 1:
                r.internal = data
            elif which == 2:
                if len(data) != 4:
                    raise Bad(E_BAD_REQUEST)
                r.flags = struct.unpack('>I', data)[0]
            else:
                raise Bad(E_BAD_REQUEST)
            log('room', hex(rid), c.label(), 'set', ('external', 'internal', 'flags')[which], hx(data, 24))
            c.reply(OK, req)
            # ROOM_DATA push: room u64 | which u8 | len u16 | data (sceNpMatching2.c:679-689). To the others only:
            # the writer raises its own event (comment at sceNpMatching2.c:601-609).
            for o in r.others(c):
                o.client.push(ROOM_DATA, struct.pack('>QB', r.id, which) + pblob(data))
        elif typ == ROOM_MESSAGE:
            self.need_hello(c)
            # room u64 | to u16 (0 = everyone) | len u16 | msg (sceNpMatching2.c:1293-1296)
            rid, to, n = struct.unpack_from('>QHH', body, 0)
            msg = body[12:12 + n]
            if len(msg) != n:
                raise Bad(E_BAD_REQUEST)
            r, m = self.room_of(c, rid)
            if to and to not in r.members:
                raise Bad(E_NOT_FOUND)
            dests = [r.members[to]] if to else r.others(c)
            c.reply(OK, req)
            # ROOM_MSG push: room u64 | from u16 | to u16 | len u16 | msg (sceNpMatching2.c:691-694)
            for d in dests:
                d.client.push(ROOM_MSG, struct.pack('>QHHH', r.id, m.mid, to, n) + msg)
        elif typ in (STREAM_CONNECT, STREAM_ACCEPT):
            # relay legs arrive on fresh connections without HELLO; HELLO_ACK told the client there's no relay
            raise Bad(E_NO_RELAY)
        else:
            # scores (0x20-0x22) and anything unknown: not served at this stage
            raise Bad(E_NOT_FOUND)

    def warn_other_rooms(self, c, rid):
        """sceNpMatching2.c keeps one room per context (parse_joined overwrites it), so a client that creates or joins
        without leaving stays a ghost member of the old room for everyone else. Logged, not changed."""
        old = [hex(r.id) for r in self.rooms.values() if r.id != rid and r.member_of(c)]
        if old:
            log('!! WARNING:', c.label(), 'creates/joins a room while still a member of', old, '(no LEAVE_ROOM sent)')

    def remove_member(self, r, m, kicked):
        """Leave, kick, or disconnect. The one removed gets KICKED (room u64, sceNpMatching2.c:716) if kicked;
        everyone left gets MEMBER_LEFT: room u64 | gone u16 | owner u16 (sceNpMatching2.c:657-659)."""
        del r.members[m.mid]
        if kicked:
            m.client.push(KICKED, struct.pack('>Q', r.id))
        if not r.members:
            del self.rooms[r.id]
            log('room', hex(r.id), 'empty -> deleted')
            return
        if r.owner == m.mid:
            r.owner = min(r.members)
            log('room', hex(r.id), 'owner', m.mid, '->', r.owner)
        log('room', hex(r.id), m.client.label(), 'member', m.mid, 'kicked' if kicked else 'left', '; %d left' % len(r.members))
        for o in r.members.values():
            o.client.push(MEMBER_LEFT, struct.pack('>QHH', r.id, m.mid, r.owner))


def main(argv):
    global LOG
    if len(argv) < 2:
        print(__doc__)
        return 2
    port = 36100
    if '--port' in argv:
        port = int(argv[argv.index('--port') + 1])
    LOG = open(argv[1], 'a', buffering=1)
    srv = Server(port)
    print('mini_psnr listening on %s:%d' % (HOST, srv.tcp.getsockname()[1]), flush=True)
    srv.run()


if __name__ == '__main__':
    sys.exit(main(sys.argv))
