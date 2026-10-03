"""EXT_UPSTREAMS(#97)の共有ヘルパ。

"/ext/{name}/**" ゲート(routers/ext.py — 人間の目)と context_images の
参照解決(utils/image_refs.py — モデルの目)が同じ基準を共有する:

- backend allowlist は env EXT_UPSTREAMS 一本(SSRF の真実源。別 allowlist は作らない)。
  値の形で backend が決まる(#139): http(s) の base URL = HTTP 中継、
  `file:///abs/path` = ローカル root からの静的配信(`ext_backend`)
- file backend が配信する範囲は `ext_file_served` 1 箇所: resolve 済みの実ファイルが root の
  中にあること、env EXT_FILE_SCOPES(`name=root からの相対ディレクトリ`)があればその
  ディレクトリの中にあること。/ext ゲート・resolver(`resolve_url`)・URL ⇔ sandbox パスの
  橋渡し(`mounted_path_for_url` / `url_for_mounted_path`)が全部これを通る — 「ブラウザには
  404 だが tool には読める」(またはその逆)を作らない。root 配下で配信範囲の外の物は
  添付としても渡さない(`ext_file_withheld`)
- env EXT_PUBLIC_BASES(`name=原本 URL のルート`)は「モデルが渡した絶対 URL が
  どの name の物か」の対応だけ(`match_public_base`)。到達先は相変わらず
  EXT_UPSTREAMS の backend — base に当たっても name が EXT_UPSTREAMS に無ければ解決しない
- path traversal 拒否は同一判定(percent デコード後の ".." セグメント・バックスラッシュ)
- httpx AsyncClient はモジュールで 1 個を共有(リクエスト毎に生成しない)。
  follow_redirects は常に False — 上流の redirect 先(内部 URL)を辿らない・漏らさない
  (院内の範囲の URL だけは resolver が転送先を範囲内に限って自分で辿る — image_refs)
- env EXT_INTRANET_CIDRS(院内のアドレス範囲)の判定もここ(`intranet_allowed` /
  `pin_intranet_address`): 範囲に含めても危ういアドレス(loopback・リンクローカル・
  docker 既定帯 等)は `INTRANET_DENIED_NETWORKS` として常に除外する。接続先は DNS で
  解決したアドレスに固定する(ホスト名で接続し直さない — DNS rebinding で範囲の外へ
  繋がせない)
"""

import asyncio
import ipaddress
import socket
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from nucllei.env import (
    EXT_FILE_SCOPES,
    EXT_INTRANET_CIDRS,
    EXT_PUBLIC_BASES,
    EXT_UPSTREAMS,
)

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


@lru_cache(maxsize=None)
def _resolved_root(value: str) -> Path:
    """`file:///abs/path` の root を **1 回だけ** resolve() する。

    配備では root 自体が symlink(compose の bind 先)でありうる。要求パスの閉じ込め
    (`is_relative_to`)は resolve 済み同士で比べないと成り立たないので、root 側も
    resolve しておく。値は起動時に env から確定していて変わらないため、要求ごとに
    resolve し直す理由が無い(strict=False — 無い root は "全 404" に倒れるだけ。
    有無は起動時に image_refs.log_mounts が WARNING で知らせる)。
    """
    return Path(value[len("file://") :]).resolve()


def ext_backend(name: str) -> tuple[str, str] | tuple[str, Path] | None:
    """name の backend を `("http", base_url)` / `("file", resolved_root)` で返す。

    map に無い name(機能無効を含む)は None。呼ぶ側は None を「無い」(404 /
    unavailable)として扱い、backend 種別で経路を分ける — 値の形の解釈をここ 1 箇所に
    置き、router と resolver が別々に文字列を見ないようにする。
    """
    value = EXT_UPSTREAMS.get(name)
    if value is None:
        return None
    if value.startswith("file://"):
        return ("file", _resolved_root(value))
    return ("http", value)


@lru_cache(maxsize=None)
def _served_roots(value: str, scopes: tuple[str, ...] | None) -> tuple[Path, ...]:
    """file backend が配信するディレクトリ(resolve 済み)。scopes が None なら root 全体、
    あれば各 `<root>/<scope>` — resolve して root の外へ出る scope(symlink)は捨てる。
    `_resolved_root` と同じく 1 回だけ resolve する(値は起動時に env から確定)。"""
    root = _resolved_root(value)
    if scopes is None:
        return (root,)
    served = []
    for scope in scopes:
        try:
            resolved = (root / scope).resolve()
        except (OSError, RuntimeError):
            continue
        if resolved.is_relative_to(root):
            served.append(resolved)
    return tuple(served)


def ext_file_served(name: str, resolved: Path) -> bool:
    """`resolved`(**resolve 済み**の実パス)が、name の file backend が配信する範囲に
    あるか。file backend の閉じ込めの権威はここ 1 箇所(module docstring)。

    - name が file backend でない(無い・http)→ False
    - EXT_FILE_SCOPES に name が無い → root の中なら True(従来どおり)
    - EXT_FILE_SCOPES に name が有る → 列挙したディレクトリのどれかの中なら True
      (有効なエントリが無ければ常に False)

    判定は resolve 後の実体で行う(URL の文字列ではなく)— symlink で範囲の外へ出る物は
    範囲外、範囲の外から中を指す symlink は実体が範囲内なので配信される。通常ファイルか
    どうかは見ない(呼び手が見る)。
    """
    backend = ext_backend(name)
    if backend is None or backend[0] != "file":
        return False
    roots = _served_roots(EXT_UPSTREAMS[name], EXT_FILE_SCOPES.get(name))
    return any(resolved.is_relative_to(root) for root in roots)


def ext_file_withheld(resolved: Path) -> bool:
    """`resolved`(**resolve 済み**の実パス)が、いずれかの file backend の root 配下に
    あって、**どの file backend の配信範囲(`ext_file_served`)にも無い** か。

    配信範囲の設定(EXT_FILE_SCOPES)を「利用者に渡してよい範囲」として 1 つの意味に
    するための、`ext_file_served` の裏返し: True の物は /ext ゲートが 404 にするだけで
    なく、tool が別の形(添付)で利用者に渡すこともしない(`image_refs.mounted_path_withheld`)。
    どの file backend の root 配下でもないパスは False(配信の設定と無関係な置き場)。
    root が入れ子で、外側の scope からは外れるが内側の backend が配信している物は False。
    """
    under_a_root = False
    for name in EXT_UPSTREAMS:
        backend = ext_backend(name)
        if backend is None or backend[0] != "file":
            continue
        if ext_file_served(name, resolved):
            return False
        under_a_root = under_a_root or resolved.is_relative_to(backend[1])
    return under_a_root


# EXT_INTRANET_CIDRS に含めても **常に除外** するアドレス(院内の範囲として宣言されても
# 取りに行かない)。配備の設定ミスで nucllei 自身・同じホストの管理口・docker の内部
# ネットワーク・クラウドのメタデータへ tool から届く口を開けないため。重なりは起動時に
# WARNING(image_refs.log_mounts)。
#
# docker の既定帯は 172.17.0.0/16 **だけ**(bridge の既定)。172.16.0.0/16 は院内の正当な
# 範囲でありうる(北大の払い出しは 172.16 のセグメント)— /12 全体を拒否しない。
INTRANET_DENIED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network(
        "0.0.0.0/8"
    ),  # "this network"(0.0.0.0 = 自ホスト扱いの OS がある)
    ipaddress.ip_network("127.0.0.0/8"),  # loopback(nucllei 自身・sandbox API 等)
    ipaddress.ip_network(
        "169.254.0.0/16"
    ),  # リンクローカル(169.254.169.254 = クラウドのメタデータ)
    ipaddress.ip_network(
        "172.17.0.0/16"
    ),  # docker の既定 bridge(cage の API 等の内部通信)
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # 予約 + broadcast 255.255.255.255
    ipaddress.ip_network("::/128"),  # unspecified
    ipaddress.ip_network("::1/128"),  # loopback
    ipaddress.ip_network("fe80::/10"),  # リンクローカル
    ipaddress.ip_network("ff00::/8"),  # multicast
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IMDS(IPv6)
)

# 院内の範囲の URL で辿る redirect の上限(一般の Web と同じ扱い — 4 回目は失敗)。
INTRANET_MAX_REDIRECTS = 3


def intranet_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """宣言された院内の範囲(EXT_INTRANET_CIDRS。パース済み)。"""
    return EXT_INTRANET_CIDRS


def intranet_configured() -> bool:
    """院内の範囲(EXT_INTRANET_CIDRS)が 1 つでも宣言されているか。未設定 = 範囲は無く、
    名前空間に当たらない絶対 URL は従来どおり `unknown_upstream`(fail closed)。"""
    return bool(EXT_INTRANET_CIDRS)


def _canonical_ip(
    address: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """文字列を IP に。IPv4-mapped IPv6(`::ffff:172.16.0.1`)は IPv4 として扱う(範囲も
    拒否リストも v4 で書かれている)。zone id(`fe80::1%eth0`)は落として判定する。"""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def intranet_allowed(address: str) -> bool:
    """アドレスが **院内の範囲内で、拒否リストに無い** か。範囲の判定の権威はここ 1 箇所。
    IP でない文字列(ホスト名)は False — 名前は先に `pin_intranet_address` で引く。"""
    ip = _canonical_ip(address)
    if ip is None:
        return False
    if any(ip in denied for denied in INTRANET_DENIED_NETWORKS):
        return False
    return any(ip in network for network in EXT_INTRANET_CIDRS)


def intranet_denied(address: str) -> bool:
    """アドレスが拒否リストに当たるか(範囲の設定とは無関係)。"""
    ip = _canonical_ip(address)
    return ip is not None and any(ip in denied for denied in INTRANET_DENIED_NETWORKS)


def intranet_overlaps_denied() -> list[tuple[str, str]]:
    """EXT_INTRANET_CIDRS のうち拒否リストと重なる (設定, 拒否) の組。起動時の WARNING 用。"""
    hits = []
    for network in EXT_INTRANET_CIDRS:
        for denied in INTRANET_DENIED_NETWORKS:
            if network.version == denied.version and network.overlaps(denied):
                hits.append((str(network), str(denied)))
    return hits


async def lookup_host(host: str) -> list[str]:
    """ホスト名を `getaddrinfo` で引いてアドレスの一覧(文字列)にする(OS の解決 =
    `/etc/hosts` も効く。閉域で hosts に書いてあれば DNS 無しでも通る)。IP リテラルは
    引かずにそのまま 1 件(`pin_intranet_address` は先に自分で判定するのでここには来ない)。
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


async def pin_intranet_address(host: str) -> tuple[str | None, str]:
    """URL のホストを DNS で引き、**接続先に固定するアドレス** を決める。

    戻り値は `(address, reason)`。成功なら `(ip, "")`。失敗は `(None, reason)` で、
    reason は閉じた集合:

    - `"unresolvable"` — DNS で引けない(NXDOMAIN・タイムアウト)。到達不能として扱う
    - `"outside_intranet"` — 引けたが、院内の範囲(拒否リスト除外後)にあるアドレスが
      1 つも無い、**または** 1 つでも拒否リストに当たるアドレスが混ざる(範囲内と
      loopback を同時に返す名前は怪しい — fail closed)

    採るのは範囲内のアドレスのうち最初の 1 つ。呼び手はこの IP に接続し、Host ヘッダ /
    SNI には元のホスト名を使う(DNS rebinding で 2 度目の解決が範囲の外を指しても、
    繋ぐ先はここで確かめた 1 つのまま)。

    ホストが IP リテラル(v4 / v6。`[]` は urlsplit が外している)なら名前解決はせず、
    そのまま範囲判定する — 閉域の本番(DNS 無し)では院内の URL はこの形が普通で、
    これが主経路。名前は `lookup_host`(getaddrinfo = /etc/hosts も効く)。
    """
    if _canonical_ip(host) is not None:
        # IP リテラル(閉域の本番 — DNS が無い — ではこれが院内の URL の普通の形)は
        # 引かずにそのまま判定する
        addresses = [host]
    else:
        addresses = await lookup_host(host)
    if not addresses:
        return None, "unresolvable"
    if any(intranet_denied(address) for address in addresses):
        return None, "outside_intranet"
    for address in addresses:
        if intranet_allowed(address):
            return address.split("%", 1)[0], ""
    return None, "outside_intranet"


def match_public_base(url: str) -> tuple[str, str] | None:
    """絶対 URL が EXT_PUBLIC_BASES のどれかの `<base>` そのもの、または `<base>/` に
    前方一致すれば `(name, rel)` を返す。rel は base の次のスラッシュから後ろ
    (path[?query][#fragment] をそのまま — 分解は呼ぶ側)。base そのもの(末尾 `/` の
    有無によらず)は path 部が空の rel になる(= その name のルート)。当たらなければ None。

    比較は urlsplit した scheme と netloc(host:port、小文字化 — URL の scheme / host は
    大文字小文字を区別しない)の一致 + path の `<base path>` 完全一致または
    `<base path>/` 前方一致。netloc は
    userinfo を含めて比べるので `http://his-info.test@evil/...` は base
    `http://his-info.test` に当たらない。base はパース時に末尾スラッシュを落として
    あるので `<base path>/` で比べることで `http://h/docs` が `http://h/docs2/...` に
    当たることは無い。複数 base に当たる場合(片方がもう片方の prefix)は長い方を採る。
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    best: tuple[str, str] | None = None
    best_len = -1
    for name, base in EXT_PUBLIC_BASES.items():
        try:
            b = urlsplit(base)
        except ValueError:
            continue
        if (
            parts.scheme.lower() != b.scheme.lower()
            or parts.netloc.lower() != b.netloc.lower()
        ):
            continue
        prefix = f"{b.path}/"
        if parts.path == b.path:
            rel = ""
        elif parts.path.startswith(prefix):
            rel = parts.path[len(prefix) :]
        else:
            continue
        if len(prefix) <= best_len:
            continue
        if parts.query:
            rel += f"?{parts.query}"
        if parts.fragment:
            rel += f"#{parts.fragment}"
        best = (name, rel)
        best_len = len(prefix)
    return best
