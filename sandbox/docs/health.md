# sandbox 健全性維持 — 設計と運用

閉域・無人・無再起動で長期運用する前提での sandbox のリソース健全性維持策。実装は
`app/main.py`、単一情報源は本ファイル。関連: [README.md](../README.md) / [STATUS.md](../STATUS.md)。

**内部 (image 側) の mechanism はこの repo、外部 (配備側) の policy・監視配線・GC の実行は
`prism-hu/llens`** という分担。以下 ④ の「外部 = policy」はそちら側にあるスクリプトを指す。

## 2 つの別リソース

混同しやすいが、増え方も対策も別。

| | loop mount (attach) | workspace `.img` |
|---|---|---|
| 実体 | `mount -o loop`(autoclear)の attach | volume 上の sparse ext4 ファイル |
| 上限 | `SANDBOX_LOOP_NODES`(既定 256) | disk(quota GB×人 が見かけ上限、実は sparse) |
| 増え方 | 稼働中に実行した distinct user 数だけ単調増加 | distinct user 数だけ永久増加 |
| 再起動で | **消える**(mount ns 破棄 → autoclear detach) | **残る**(volume 永続) |
| 対策 | **① idle-detach reaper**(内部) | **④ stale-img GC**(外部 policy + 内部 mechanism) |

loop = 「稼働中の瞬間ピーク」問題。`.img` = 「累積ディスク/クラッタ」問題。

## ① idle-detach reaper(内部)

`_ensure_workspace` は `mount -o loop` するだけで umount を持たず、loop は単調増加する。
idle 超過した workspace を umount して budget を非単調化する。

- **契機**: 既存 `_reaper_loop`(300s 周期)に相乗り。`SANDBOX_WS_IDLE_TTL_S`(既定 3600s)超過で umount。
- **detach は umount のみ**: `mount -o loop` は autoclear を張るので、umount で最後の close 時に
  カーネルが自動 detach する。`losetup -d` は呼ばない。
- **umount ガード(全て満たすまで umount しない)**:
  1. idle 超過(`last_used`)
  2. `_mount_lock(user)` が未 locked(mount/remount 進行中でない)
  3. ロック取得後に idle を再判定(競合直後の touch を拾う)
  4. **生存 session 無し(hard gate)**: stateful kernel は jail に `--bindmount ws:/workspace` で
     ws を差しており、生存中に umount すると (a) jail が superblock を掴んだままで loop が
     解放されず、(b) 次アクセスで同一 .img を新 loop で二重 mount → **ext4 split-brain 破損**する
  5. stateless 実行中でない(`_user_lock` 未 locked。主保証は TTL≫実行秒、これは belt-and-suspenders)
  6. `ismount` 真(既に非 mount なら registry から落としてスキップ)
  7. umount が EBUSY 等で失敗したら umount せず次ラウンドへ
- **再 mount**: `_ensure_workspace` の `ismount` 判定が未 mount なら自動で貼り直す(往復が閉じる)。
- **ロック順**: `get()` は `SM._lock → _mount_lock` の順。**reaper は `SM._lock` を取らない**
  (逆順デッドロック回避)。生存 session 判定は `_sessions._sessions` を await なしで読むだけ。
- `_mount_locks` は pop しない(待機中 coroutine がいると別 lock が作られ並行 mount で破損する。
  leak は user 数ぶんで極小)。

## ② GET /metrics(内部)

Prometheus text 形式を**依存を足さず手書き**(`PlainTextResponse`)。全 gauge は label なし
(未認証 endpoint で user_id を出さない / cardinality 爆発を避ける)。

| gauge | 算出 |
|---|---|
| `sandbox_loop_used` | `/workspaces` 直下の mountpoint 数。**host snapd の loop は構造上入らない**(snap は `/workspaces` 配下に mount されない)。losetup パースより正確・drift なし |
| `sandbox_loop_nodes_total` | `SANDBOX_LOOP_NODES`(alert 分母を metric 側に持たせ Grafana に定数を焼かない) |
| `sandbox_workspaces_total` | `.images/*.img` 数(未 mount 含む = ④ GC の分母) |
| `sandbox_sessions_active` | 生存 kernel session 数 |
| `sandbox_disk_used_bytes` / `_total_bytes` | `statvfs(/workspaces)`。**`/` には nucllei-db(Postgres)も同居**するため disk 監視は必須(満杯で DB ごと落ちる) |

- **`/metrics` は認証免除のまま**(#132 の共有トークンを設定した配備でも。未設定の配備は
  そもそも全 endpoint が無認証):
  返すのは label なしの集約 gauge だけで user_id を含まず厳密に低感度。認証すると配備側の
  Prometheus にトークン配布(`bearer_token` 設定)が要り、監視のために秘密を 1 箇所増やす。
  免除は `main.py` の `PUBLIC_PATHS`(`/healthz` と 2 つだけ)。
- Prometheus job(既定 bridge・service DNS): `targets: ["sandbox:8000"]`。
- Grafana alert: loop `loop_used/loop_nodes_total > 0.75`(host snap baseline を吸収した実効 80%)、
  disk `disk_used/disk_total > 0.80`。

## ④ stale-img GC(外部 policy + 内部 mechanism)

破壊操作を外部スクリプトが fs 直で行うと、内部の umount/remount と直列化できず TOCTOU
(判定→unlink の窓で app が remount / 活き .img を消す)になる。責務を分離する。

- **内部 = mechanism**: `DELETE /workspace/{user_id}`。`_mount_lock` 下で live-session/busy/ismount を
  確認 → umount → `.img` は即 unlink せず **trash rename**(`.trash-<ts>`、回復余地)。
- **外部 = policy** (配備層 = llens 側のスクリプト): nucllei-db を引いて
  「存在する user との set 差分 = 削除済 user」のみ対象
  (inactive は復帰しうるので対象外)。**0 件/閾値未満で abort**(DB 不達での全削除防止)、
  `.img` mtime の min-age を AND(時刻**単独**判定はしない)。dry-run 既定 → `--apply` で内部 DELETE を呼ぶ。
  JSONL 監査ログ。**fs は直接触らない**。**#132 の `SANDBOX_TOKEN` を設定した配備では、
  内部 DELETE の呼び出しに `Authorization: Bearer <SANDBOX_TOKEN>` が要る**
  (sandbox と同じ値を配備側で渡す。認証は opt-in なので未設定の配備ではヘッダ不要)。
- app は nucllei の DB を一切知らない(user_id を opaque token として受けるだけ)。
  privileged コンテナに DB 資格情報を持たせない。

## C9 brick 耐性 / C5 枯渇応答

- **C9**: mkfs は journal 無し(`-O ^has_journal`)。停電等 unclean shutdown 後に `e2fsck -p` が
  自動修復不能(rc≥2)になりうる。恒久 500 を避け、破損 `.img` を退避(`.corrupt-<ts>`)して
  fresh 再作成する(データは失うが service は継続)。`.img` 生成は tmp→`os.replace` の atomic rename で
  半生成 poison を残さない。
- **C5**: loop/disk 枯渇の mount 失敗は opaque 500 でなく **503**(「同時利用ユーザー数の上限」)に変換。
  枯渇は ① 導入後も active user > 実効 budget で起きる終端状態。

## ⑤ loop ドライバ実効チェック(healthz)

loop ドライバ自体が使えない状態は、①〜④ が守る「リソースの増減」と別軸の **hard fault**
(全 workspace mount が失敗 = 全実行が 5xx)。かつてこの状態でも healthz は `ok:true` を
返し続け、`docker ps` は healthy のまま障害が不可視だった(2026-08-10 実障害)。

- **検出**: `/dev/loop-control` を open して確かめる(`_loop_available()`)。ノードの存在
  チェックでは検出できない — ドライバ不在でも devtmpfs のノードは残るため、開いて
  ENODEV になるかが唯一の実効判定。
- **応答**: healthz が `{ok:false, loop:false}` + **503** → compose healthcheck が落ち、
  コンテナが unhealthy になる。起動時にも同判定で WARNING ログを出す。
- **典型例**: ホストのカーネルパッケージ更新が稼働中カーネルの module ツリーを消し、
  そのまま再起動していない場合(ローリングリリースの開発機で発生。閉域本番は
  「閉域後の更新はしない」運用のためまず踏まない)。`modprobe loop` が
  `Module not found` を返すならこれ — **ホストの reboot が必要**(応急処置は
  パッケージキャッシュから稼働カーネル版 `loop.ko` を取り出して insmod)。
- **C5 との切り分け**: mount 失敗はどちらも 503「同時利用ユーザー数の上限」に丸まる。
  healthz の `loop:false` なら混雑ではなく恒久障害なので、再試行の案内は当てにならない。

## env 一覧(既定で運用可)

| env | 既定 | 意味 |
|---|---|---|
| `SANDBOX_LOOP_NODES` | 256 | loop ノード上限。実効は host snap ぶん(~14)引いた ≈242。壁を上げる保険に増やせる(根治は ①) |
| `SANDBOX_WS_IDLE_TTL_S` | 3600 | この秒数 idle で workspace を umount。`TIMEOUT_MAX_S`(600)より長く `SESSION_TTL_S`(43200)より短く |

## 運用メモ

- loop 監視は `sandbox_loop_used` を見る(`losetup \| wc -l` は host snap が混ざるので使わない)。
- コンテナ再起動で workspace loop は全リセット(autoclear)。`.img` は残る。
- 枯渇時の即応: コンテナ再起動で loop クリア / `SANDBOX_LOOP_NODES` 引き上げ。
- 搬入前 (配備側の preflight): 実 kernel で 256 loop attach 可能を実測、本番ホストの
  volume 実配置(`/` 同居)を確認。
