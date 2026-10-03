"""`/ext/**`(#97 / #139)と一般 URL 取得の共有ヘルパ。

"/ext/**" ゲート(routers/ext.py — 人間の目)と context_images の参照解決
(utils/image_refs.py — モデルの目)が同じ基準を共有する:

- 配信する場所に設定は無い(ken 決定 2026-10-03): `EXT_DIR`(`image_refs.EXT_DIR`)
  に置いたものを、そのままの相対パスで `/ext/<rel>` に配信する
  (`ext/cage/static/X` ⇔ `/ext/cage/static/X`。sandbox は同じ物を同じ文字列
  `/ext/cage/static/X` に見る)。閉じ込めは `image_refs` の `_read_confined` と同じ(resolve 済みの実体が
  `EXT_DIR` の中の通常ファイル)。2026-10-03 までの
  `EXT_UPSTREAMS`(http 中継 / `file://` root)・`EXT_FILE_SCOPES`(配信範囲)・
  `EXT_PUBLIC_BASES`(原本 URL のルート)は、`url=` の一般 URL がどこでも取れるようになった
  のと同じ日に撤去した — http 中継も原本ルートの対応も要らなくなった
- path traversal 拒否は同一判定(percent デコード後の ".." セグメント・バックスラッシュ)
- httpx AsyncClient はモジュールで 1 個を共有(リクエスト毎に生成しない)。
  follow_redirects は常に False — resolver が転送先を自分で辿る(image_refs。各ホップで
  自分自身の判定を掛けるため)
- 一般の URL(`/ext/` 形でない絶対 URL)で **取りに行かない相手はこのサーバ自身(と同居する
  サービス)だけ**(`self_addresses` / `is_self_address` / `pin_address`、2026-10-03 ken
  決定)。行き先で機能を絞らない — 届くかどうかは配備のネットワークが決める(閉域なら外には
  届かない。それは prompt / skill の文であって tool のゲートではない)。拒否する集合は
  **起動後に実行時に求める**(固定 CIDR は書かない — 本番の docker bridge は 10.106.x で、
  172.17/16 を固定で書いても host には届いてしまう)+ env SELF_ADDRESSES。接続先は DNS で
  解決したアドレスに固定する(ホスト名で接続し直さない — DNS rebinding で 2 度目の解決が
  自分自身を指しても繋がない)
"""

import asyncio
import ipaddress
import logging
import socket
import time
from urllib.parse import unquote

import httpx

log = logging.getLogger(__name__)

# 接続 5s / 読み 30s。上流はローカル(Docker bridge)想定 — 不達・遅延で
# ワーカーを長く掴ませない。
EXT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class _NoCookies(httpx.Cookies):
    """上流の Set-Cookie を一切保持・再送しない cookie jar。

    共有 AsyncClient を全ユーザー・全リクエストで使い回すため、既定 jar だと
    上流の Set-Cookie が蓄積して別ユーザーのリクエストに混入する(#97 review)。
    extract(保存)/ set_cookie_header(送出)を no-op 化して stateless に保つ。
    """

    def extract_cookies(self, response: httpx.Response) -> None:  # noqa: D102
        return None

    def set_cookie_header(self, request: httpx.Request) -> None:  # noqa: D102
        return None


_client: httpx.AsyncClient | None = None


def get_ext_client() -> httpx.AsyncClient:
    """共有 AsyncClient(遅延生成)。テストは `_client` を MockTransport 付きの
    インスタンスへ差し替える。cookie は保持しない(_NoCookies)。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=False, timeout=EXT_TIMEOUT, cookies=_NoCookies()
        )
    return _client


def has_path_traversal(path: str) -> bool:
    """上流へ渡してはいけないパスか(traversal / 制御文字)。

    - ".." セグメント(percent デコード後)またはバックスラッシュ → 拒否。
      多重エンコード("..%252F" 等)対策でデコードは安定するまで繰り返す。
    - ASCII 制御文字・非印字(CR/LF/NUL/TAB 等)→ 拒否。これらは httpx の
      URL 構築を InvalidURL で落とし(funnel を逸れて 500 になる)、CRLF は
      ヘッダインジェクションの臭いでもあるため、共有ゲートで fail-closed する
      (#97 review — プロキシは 400、inline は "(image unavailable)" へ合流)。

    判定は拒否のためだけ — 上流へはデコード前のパスをそのまま渡す。
    """
    decoded = path
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in decoded):
        return True
    if "\\" in decoded:
        return True
    return ".." in decoded.split("/")


# --- 自分自身(と同居するサービス)の拒否 ---------------------------------------------
#
# 登録の無い一般の URL で唯一取りに行かない相手。2 層:
#
# 1. 固定の集合 `_SELF_FIXED_NETWORKS` — **正当な行き先になり得ないアドレス**(どの配備でも
#    「自分自身」か「宛先として無意味」): loopback・unspecified・リンクローカル(クラウドの
#    メタデータ 169.254.169.254 を含む)・multicast / 予約。**ネットワークポリシーではない** —
#    private 帯(10/8・172.16/12・192.168/16)は MiNet の本物の院内なので、ここに書かない。
#    docker bridge の既定帯 172.17/16 も書かない(本番の bridge は 10.106.x — 固定で書くと
#    守れない上に正当な院内を巻き込む)
# 2. **実行時にしか分からない物**(`discover_self_addresses`)— このコンテナ / ホストの自分の
#    アドレス、既定ゲートウェイ(= docker bridge のホスト側)、`host.docker.internal` の
#    解決結果。後者を固定 CIDR で書かないのが要点(module docstring)。

_SELF_FIXED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),  # unspecified("this network")
    ipaddress.ip_network("127.0.0.0/8"),  # loopback(nucllei 自身・sandbox API 等)
    ipaddress.ip_network(
        "169.254.0.0/16"
    ),  # リンクローカル(169.254.169.254 = メタデータ)
    ipaddress.ip_network("224.0.0.0/4"),  # multicast(宛先になり得ない)
    ipaddress.ip_network("240.0.0.0/4"),  # 予約 + broadcast(宛先になり得ない)
    ipaddress.ip_network("::/128"),  # unspecified
    ipaddress.ip_network("::1/128"),  # loopback
    ipaddress.ip_network("fe80::/10"),  # リンクローカル
    ipaddress.ip_network("ff00::/8"),  # multicast(宛先になり得ない)
)

# 実行時に引く docker の特別名(ホスト側 = bridge のゲートウェイ)。引けなければ無視。
_SELF_HOST_NAMES = ("host.docker.internal", "gateway.docker.internal")

# 発見結果のキャッシュ TTL(秒)。インターフェースやルートは稼働中にほぼ変わらないが、
# プロセス起動時に 1 回だけだと compose のネットワーク付け替えに追従しないので短く持つ。
SELF_ADDRESSES_TTL = 60.0

# 一般の URL で辿る redirect の上限(一般の Web と同じ扱い — 4 回目は失敗)。
MAX_REDIRECTS = 3


def _canonical_ip(
    address: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """文字列を IP に。IPv4-mapped IPv6(`::ffff:127.0.0.1`)は IPv4 として扱う(固定の
    集合も自分のアドレスも v4 で持つ)。zone id(`fe80::1%eth0`)は落として判定する。"""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read().split("\n")
    except OSError:
        return []


def _proc_fib_trie_locals() -> set[str]:
    """`/proc/net/fib_trie` から自分の IPv4 アドレス(`/32 host LOCAL` の葉)を拾う。
    Linux だけ。無ければ空。"""
    found: set[str] = set()
    last = None
    for line in _read_lines("/proc/net/fib_trie"):
        s = line.strip()
        if s.startswith("|--") or s.startswith("+--"):
            last = s.split()[1].split("/")[0]
        elif "LOCAL" in s and s.startswith("/32") and last:
            found.add(last)
    return found


def _proc_if_inet6_locals() -> set[str]:
    """`/proc/net/if_inet6` から自分の IPv6 アドレスを拾う(32 桁 hex + ifindex + prefix +
    scope + flags + name)。Linux だけ。無ければ空。"""
    found: set[str] = set()
    for line in _read_lines("/proc/net/if_inet6"):
        fields = line.split()
        if not fields or len(fields[0]) != 32:
            continue
        try:
            found.add(str(ipaddress.IPv6Address(int(fields[0], 16))))
        except ValueError:
            continue
    return found


def _proc_default_gateways() -> set[str]:
    """`/proc/net/route`(IPv4。Destination 0 かつ RTF_GATEWAY)と `/proc/net/ipv6_route`
    (dest ::/0 かつ RTF_GATEWAY)から既定ゲートウェイを拾う。docker の bridge では
    これがホスト側のアドレス(= 同居するサービスの口)。Linux だけ。無ければ空。"""
    found: set[str] = set()
    for line in _read_lines("/proc/net/route")[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            dest, gateway, flags = fields[1], int(fields[2], 16), int(fields[3], 16)
        except ValueError:
            continue
        if dest == "00000000" and flags & 0x2 and gateway:
            # little-endian hex → dotted quad
            found.add(str(ipaddress.IPv4Address(socket.htonl(gateway) & 0xFFFFFFFF)))
    for line in _read_lines("/proc/net/ipv6_route"):
        fields = line.split()
        if len(fields) < 9 or len(fields[0]) != 32 or len(fields[4]) != 32:
            continue
        try:
            dest_prefix, flags = int(fields[1], 16), int(fields[8], 16)
            if int(fields[0], 16) == 0 and dest_prefix == 0 and flags & 0x2:
                found.add(str(ipaddress.IPv6Address(int(fields[4], 16))))
        except ValueError:
            continue
    return found


def _resolve_all(name: str) -> set[str]:
    """名前を getaddrinfo で引いてアドレスの集合に(引けなければ空)。blocking。"""
    try:
        infos = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return set()
    return {info[4][0] for info in infos}


def discover_self_addresses() -> (
    frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]
):
    """「このサーバ自身(と同居するサービス)」を指すアドレスを **今の環境から** 求める
    (blocking。`self_addresses` が TTL 付きで呼ぶ。テストはこちらを差し替える)。

    集める物(どれも無ければ無視 — 失敗で例外にしない):

    - 自分のインターフェースの全アドレス: `/proc/net/fib_trie`(v4)+ `/proc/net/if_inet6`
      (v6)。補助に `getaddrinfo(gethostname())`(/proc の無い OS でも最低限これは取れる)
    - 既定ゲートウェイ(v4 / v6): `/proc/net/route` / `/proc/net/ipv6_route`。docker の
      コンテナではこれが bridge のホスト側 = ホストで待ち受ける同居サービス(cage の API 等)
    - `host.docker.internal` / `gateway.docker.internal` の解決結果(Docker Desktop や
      `extra_hosts: host-gateway` の配備で引ける。引けなければ無視)

    **限界**(固定 CIDR を書かずに実行時に求める代償):

    - Linux 以外(macOS の開発機等)では /proc が無いので、インターフェースの列挙は
      `gethostname()` の解決結果だけになり、既定ゲートウェイは取れない
    - ホストが **別のインターフェース** で待ち受ける同居サービス(bridge のゲートウェイでも
      コンテナの自分のアドレスでもない、ホストの LAN 側アドレス `10.106.x.y` 等)は、ホスト
      ネットワークで動かす・`extra_hosts` で名前を与える、のどちらかでないと拾えない。
      そのような配備では同居サービス側で認証を持つこと(sandbox の SANDBOX_TOKEN と同じ)
    - 固定の集合(loopback・リンクローカル等)は `_SELF_FIXED_NETWORKS` で別に持つ —
      ここには含めない
    """
    raw: set[str] = set()
    raw |= _proc_fib_trie_locals()
    raw |= _proc_if_inet6_locals()
    raw |= _proc_default_gateways()
    try:
        raw |= _resolve_all(socket.gethostname())
    except OSError:
        pass
    for name in _SELF_HOST_NAMES:
        raw |= _resolve_all(name)
    found = set()
    for address in raw:
        ip = _canonical_ip(address)
        if ip is not None:
            found.add(ip)
    return frozenset(found)


_self_cache: tuple[float, frozenset] | None = None


def self_addresses() -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """`discover_self_addresses` の結果を `SELF_ADDRESSES_TTL` 秒キャッシュして返す。
    blocking(発見は /proc の読みと getaddrinfo)— async からは thread で呼ぶ。"""
    global _self_cache
    now = time.monotonic()
    if _self_cache is not None and now - _self_cache[0] < SELF_ADDRESSES_TTL:
        return _self_cache[1]
    found = discover_self_addresses()
    _self_cache = (now, found)
    return found


def reset_self_addresses() -> None:
    """キャッシュを捨てる(テスト・起動ログ用)。"""
    global _self_cache
    _self_cache = None


def is_self_address(address: str) -> bool:
    """アドレスが **このサーバ自身(と同居するサービス)** を指すか — 固定の集合
    (`_SELF_FIXED_NETWORKS`)か、実行時に求めた自分のアドレス(`self_addresses`)。
    IP でない文字列(ホスト名)は False — 名前は先に `pin_address` で引く。blocking
    (初回・TTL 切れで発見が走る)。"""
    ip = _canonical_ip(address)
    if ip is None:
        return False
    if any(ip in network for network in _SELF_FIXED_NETWORKS):
        return True
    # 配備が足した host の外向き IP(`SELF_ADDRESSES`、nucllei.env)。import 時でなく
    # 呼び出し時に引く — テストが env を差し替えられるように
    from nucllei.env import SELF_ADDRESSES

    if ip in SELF_ADDRESSES:
        return True
    return ip in self_addresses()


async def lookup_host(host: str) -> list[str]:
    """ホスト名を `getaddrinfo` で引いてアドレスの一覧(文字列)にする(OS の解決 =
    `/etc/hosts` も効く。閉域で hosts に書いてあれば DNS 無しでも通る)。IP リテラルは
    引かずにそのまま 1 件(`pin_address` は先に自分で判定するのでここには来ない)。
    引けなければ空。テストはこの関数を差し替える(実 DNS に出ない)。"""
    if _canonical_ip(host) is not None:
        return [host]
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = sockaddr[0]
        if address not in addresses:
            addresses.append(address)
    return addresses


async def pin_address(host: str) -> tuple[str | None, str]:
    """URL のホストを DNS で引き、**接続先に固定するアドレス** を決める。

    戻り値は `(address, reason)`。成功なら `(ip, "")`。失敗は `(None, reason)` で、
    reason は閉じた集合:

    - `"unresolvable"` — DNS で引けない(NXDOMAIN・タイムアウト)。到達不能として扱う
    - `"server_self"` — 引けたアドレスの **1 つでも** このサーバ自身(と同居するサービス)を
      指す(`is_self_address`)。外のアドレスと loopback を同時に返す名前は怪しい — fail closed

    採るのは最初のアドレス(getaddrinfo の順 = OS の優先順)。呼び手はこの IP に接続し、
    Host ヘッダ / SNI には元のホスト名を使う(DNS rebinding で 2 度目の解決が自分自身を
    指しても、繋ぐ先はここで確かめた 1 つのまま)。

    ホストが IP リテラル(v4 / v6。`[]` は urlsplit が外している)なら名前解決はせず、
    そのまま判定する — 閉域の本番(DNS 無し)では院内の URL はこの形が普通で、これが
    主経路。名前は `lookup_host`(getaddrinfo = /etc/hosts も効く)。
    """
    if _canonical_ip(host) is not None:
        addresses = [host]
    else:
        addresses = await lookup_host(host)
    if not addresses:
        return None, "unresolvable"
    # 自分のアドレスの発見は blocking(/proc + getaddrinfo)— event loop を止めない
    denied = await asyncio.to_thread(
        lambda: [address for address in addresses if is_self_address(address)]
    )
    if denied:
        return None, "server_self"
    return addresses[0].split("%", 1)[0], ""
