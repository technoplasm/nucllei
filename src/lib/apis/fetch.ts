/**
 * llens(#70 B2/B3): $lib/apis 共通の fetch ラッパ。
 *
 * B2 — セッション失効の一元ハンドリング:
 *   401/403 を見ても即ログアウトにはしない(この codebase は権限拒否にも 401 を使う)。
 *   代わりに /auths/(me)でセッション自体を再検証し、セッションが死んでいるときだけ
 *   「セッション切れ」フラグを sessionStorage に置いて /auth?redirect= へハード遷移する
 *   (auth ページが onMount でフラグを読んで toast を出す)。
 *
 * B3 — バックエンド不達の可視化:
 *   fetch 自体の失敗(network error)と 502/503/504(リバプロが backend 死亡時に返す)を
 *   backendUnreachable ストアに反映し、/health のポーリングで自動復帰する。
 *   root layout がバナーを表示する。
 *
 * アイドルタイムアウト(共有 PC の離席対策、_memos/auth-profile.md):
 *   `SESSION_IDLE_TIMEOUT` は「発行からの絶対期限」ではなく「無操作でこの時間が経ったら切れる」。
 *   その判定に要る「人の操作があったか」をここ(全 API が通る唯一の口)で持つ。
 *   - FE タイマで切ったときは、`/auth` へ遷移する前に signout を叩いてサーバ側セッションも
 *     落とす(#137)。失効の判断が FE 側で起きた以上、サーバ側を道連れにするのは FE の責任。
 *   - 送信側: 最後の延命以降に操作があったリクエストにだけ ACTIVITY_HEADER を付ける。
 *     チャット画面の 5 秒ポーリングのような機械的なリクエストには付かないので、
 *     タブを開いたまま離席しても延命されない(将来ポーリングが増えても安全側)。
 *   - 画面側: サーバで切れても、アイドル中のチャットはリクエストを 1 本も出さないため
 *     前の人の画面が残り続ける。startIdleWatch() が経過時間を見て自分でログイン画面へ逃がす。
 *   - 逆に「操作はしているがリクエストが出ない」(スクロールして読む・長文を打つ)ときは、
 *     **操作した瞬間に、サーバの token の年齢が延命の刻み(renewStepMs = min(60 秒, TTL/10))を
 *     過ぎていれば 1 本だけ**延命のリクエスト(sendKeepalive)を出す。年齢はサーバが返す期限
 *     (SESSION_EXPIRES_HEADER)で測る(#154)。操作が無ければ 1 本も出ない。
 *   - 複数タブ(#154): 最終操作時刻とサーバの期限は localStorage でタブ間に共有する。
 *     cookie は全タブ共通なので、アイドル判定は「**どのタブでも**操作が無い」ときだけ —
 *     放置タブのアイドル判定が signout で作業中のタブを道連れにしない。
 *   - 「人の操作」には、マイクの音声入力で発話を検出している間も含める(noteInteraction を
 *     VoiceRecording が呼ぶ)。録音バーが開いているだけの無音は含めない。
 *   - 残り時間は getIdleRemaining() が出す。失効の判定と画面右上のリング(IdleTimeoutRing)が
 *     同じこれを使い、判定は残りがちょうど 0 になる時刻に予約する — リングが 0 になる瞬間に切れる。
 *     残りは「FE の期限(最後の操作 + TTL)」と「知っているサーバの期限」の**早い方** —
 *     サーバ側が先に切れるなら、その時間をリングに出さない。
 */
import { get } from 'svelte/store';

import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
import { user, backendUnreachable } from '$lib/stores';

export const SESSION_EXPIRED_FLAG = 'llens:session-expired';

/**
 * 「このリクエストは人の操作を伴う」ことをサーバへ伝えるヘッダ。
 * BE 側の対応は nucllei/utils/auth.py の SESSION_ACTIVITY_HEADER。
 */
export const ACTIVITY_HEADER = 'X-Nucllei-Activity';

/**
 * 操作ヘッダ付きのリクエストへの応答に BE が載せる「今の token の期限」(`exp`、epoch 秒)。
 * 延命(再発行)したかどうかに関わらず付く。BE 側は nucllei/utils/auth.py の SESSION_EXPIRES_HEADER。
 */
export const SESSION_EXPIRES_HEADER = 'X-Nucllei-Session-Expires';

// タブ間で共有する値(localStorage)。どちらもこのブラウザの時計での epoch ミリ秒。
export const SHARED_INTERACTION_KEY = 'nucllei:idle:last-interaction';
export const SHARED_EXPIRES_KEY = 'nucllei:idle:session-expires';
export const SHARED_EXTENSION_KEY = 'nucllei:idle:extension-requested';

let sessionExpired = false;
let verifying: Promise<void> | null = null;

// 最後の延命以降に人の操作があったか(リクエストにヘッダを付けたら降ろす)
let interacted = false;
// このタブで最後に人の操作があった時刻(他タブの操作は含まない)。古くなった操作を
// 後のリクエストに運ばせないために使う(withActivityHeader)
let localInteractionAt = 0;
// 最後に活動ヘッダを**実際に運べた**時刻(keepalive でも通常の API 呼び出しでもよい)。
// サーバの期限が分からない(期限ヘッダを返さない古い BE)ときの keepalive の判定にだけ使う。
// `0` = このタブではまだ 1 本も運べていない。
let lastActivitySentAt = 0;
// 最後に keepalive を出した時刻(連打防止 — sendKeepalive の説明)
let lastKeepaliveAt = 0;
// 全タブで最後に keepalive(= 延命が要るときだけ出す 1 本)を出した時刻。応答を待つ間は
// サーバの期限を残り時間に使わない(idleSnapshot)。他タブの値は readShared() で取り込む
let extensionRequestedAt = 0;
// 最後に知ったサーバの期限(このブラウザの時計に直した epoch ミリ秒。`0` = 不明)。
// 他タブが知った値は readShared() で取り込む。
let knownExpiresAt = 0;
// サーバの時計 − このブラウザの時計(ミリ秒)。同一オリジンの応答の `Date` から測る。
// サーバが返す期限(epoch 秒)を手元の時計に直すのに使う(`0` = ずれ無しとみなす)
let serverClockOffsetMs = 0;
// 最後に人の操作があった時刻(FE 側アイドルタイマの起点)。
// 残り時間ではなく**時刻の差**で判定するので、スリープ・背面タブでタイマが
// 飛んでも復帰時点で正しく評価できる。他タブの操作は readShared() で取り込む。
let lastInteractionAt = Date.now();
// localStorage への書き込みの間引き(keydown のたびに全タブへ storage イベントを撒かない)
let lastSharedWriteAt = 0;
const SHARED_WRITE_MIN_MS = 1000;

/* ---------------------------------------------------------------- *
 * タブ間共有(#154)
 *
 * cookie(= サーバのセッション)はタブ間で 1 つなのに、最終操作時刻がタブごとだと
 * 放置タブ B のアイドル判定が signout で cookie を消し、作業中のタブ A を道連れにする。
 * 判断の直前(アイドル判定・keepalive)に localStorage を読み直して取り込む —
 * `storage` イベントを待つより単純で、イベントの取りこぼしも起きない。
 * localStorage はプライベートモード・ポリシー等で例外になりうるので必ず try/catch し、
 * 使えなければタブ単独の判定(従来どおり)に落ちる。
 * ---------------------------------------------------------------- */

const readSharedNumber = (key: string): number => {
	try {
		const value = Number(localStorage.getItem(key));
		return Number.isFinite(value) && value > 0 ? value : 0;
	} catch {
		return 0;
	}
};

const writeShared = (key: string, value: number) => {
	try {
		localStorage.setItem(key, String(value));
	} catch {
		// 共有できないだけ(タブ単独で判定する)
	}
};

const removeShared = (key: string) => {
	try {
		localStorage.removeItem(key);
	} catch {
		// 共有できないだけ
	}
};

/** 他タブの最終操作時刻・サーバの期限を取り込む。 */
const readShared = () => {
	// 未来の時刻は信用しない(同じ時計なので本来起きない)
	const interaction = Math.min(readSharedNumber(SHARED_INTERACTION_KEY), Date.now());
	if (interaction > lastInteractionAt) {
		lastInteractionAt = interaction;
	}
	// 期限は「最後に知った値」をそのまま採る(max にしない)。cookie は 1 つなので最新の
	// 応答が正。同じセッションの中では期限は延命で延びる一方なので、古い値は短め =
	// keepalive を早めに出す側に倒れる。前のセッション(ログアウト → 再ログイン)の値が
	// 残っている場合は、監視の開始時に seedServerExpiry() が新しいセッションの期限で上書きする
	// (残り時間はサーバの期限も見るので、古い値のままだとログイン直後に追い出してしまう)。
	const expires = readSharedNumber(SHARED_EXPIRES_KEY);
	if (expires) {
		knownExpiresAt = expires;
	}
	const extension = Math.min(readSharedNumber(SHARED_EXTENSION_KEY), Date.now());
	if (extension > extensionRequestedAt) {
		extensionRequestedAt = extension;
	}
};

const shareInteraction = () => {
	if (lastInteractionAt - lastSharedWriteAt < SHARED_WRITE_MIN_MS) {
		return;
	}
	lastSharedWriteAt = lastInteractionAt;
	writeShared(SHARED_INTERACTION_KEY, lastInteractionAt);
};

/**
 * 同一オリジンの応答の `Date` でサーバとの時計のずれを測り直す。サーバの時計と
 * ずれていても残り時間が正しくなるよう、サーバが返す期限はこれで手元の時計に直す。
 * (`Date` は秒単位なので 1 秒程度の誤差はある — 期限を長めに見る向き)
 */
const noteServerClock = (input: RequestInfo | URL, res: Response) => {
	if (res.status === 304 || !isSameOrigin(input)) {
		return;
	}
	const serverNow = Date.parse(res.headers?.get?.('Date') ?? '');
	if (Number.isFinite(serverNow)) {
		serverClockOffsetMs = serverNow - Date.now();
	}
};

/** サーバの epoch 秒を、このブラウザの時計の epoch ミリ秒に直す。 */
const serverSecondsToLocalMs = (epochSeconds: number) => epochSeconds * 1000 - serverClockOffsetMs;

/** 応答の期限ヘッダを覚える(全タブへ共有する)。 */
const noteServerExpiry = (res: Response) => {
	const raw = res.headers?.get?.(SESSION_EXPIRES_HEADER);
	const exp = raw ? Number(raw) : NaN;
	if (!Number.isFinite(exp) || exp <= 0) {
		return;
	}
	knownExpiresAt = serverSecondsToLocalMs(exp);
	writeShared(SHARED_EXPIRES_KEY, knownExpiresAt);
};

/**
 * 「人の操作があった」と記録する(アイドルタイマの起点を進め、次のリクエストに延命ヘッダを付ける)。
 *
 * 下の DOM イベントのほかに、**イベントにならない在席の証拠**を持つ側が呼ぶ — マイクの音声入力で
 * 発話を検出している間(VoiceRecording)。喋っているだけの間はキーもクリックも無いので、
 * 呼ばないと長い口述の途中でログイン画面へ追い出され、読み取った文が失われる。
 * 呼ぶ側は「人がそこに居る」と言えるときだけ、間引いて呼ぶこと(機械的な周期で呼ぶと
 * 「ポーリングでは延命しない」が崩れる)。
 */
export const noteInteraction = () => {
	interacted = true;
	lastInteractionAt = Date.now();
	localInteractionAt = lastInteractionAt;
	shareInteraction();
	// 延命の 1 本は**操作した瞬間**に出す(理由は sendKeepalive の説明)
	sendKeepalive();
};

if (typeof window !== 'undefined') {
	// 「操作」= 明確な意図のある入力だけ。**`mousemove` は除外・`wheel` は含める** —
	// 机にぶつかってカーソルが動いただけを在席と見なしたくない一方、ホイールは
	// 「長い回答をスクロールして読んでいる = 在席している」の代表例だから。
	for (const type of ['keydown', 'pointerdown', 'click', 'touchstart', 'wheel']) {
		window.addEventListener(type, noteInteraction, { passive: true, capture: true });
	}
}

/** 同一オリジンのときだけヘッダを足す(外部宛に無用な preflight を出さない)。 */
const isSameOrigin = (input: RequestInfo | URL): boolean => {
	try {
		const url =
			input instanceof Request ? input.url : input instanceof URL ? input.href : String(input);
		return new URL(url, location.href).origin === location.origin;
	} catch {
		return false;
	}
};

const withActivityHeader = (
	input: RequestInfo | URL,
	init?: RequestInit
): RequestInit | undefined => {
	if (!interacted || typeof window === 'undefined' || !isSameOrigin(input)) {
		return init;
	}
	interacted = false;
	// 操作から刻み(renewStepMs)より後のリクエストには運ばせない。運ぶと BE はその
	// リクエストの時刻から TTL で再発行し、サーバの期限が FE の期限(操作 + TTL)を
	// 追い越す(#137 の signout が後で落とすが、追い越し自体を刻み以内に抑える)。
	// 運ばなくても失うものは無い — 延命が要る操作なら、その瞬間に keepalive が出ている
	// (出ていない = その時点で token の年齢が刻み未満 = サーバの期限のずれは刻み以内)。
	if (
		idleTimeoutSeconds &&
		Date.now() - localInteractionAt > renewStepMs(idleTimeoutSeconds * 1000)
	) {
		return init;
	}
	lastActivitySentAt = Date.now();

	const headers = new Headers(
		init?.headers ?? (input instanceof Request ? input.headers : undefined)
	);
	headers.set(ACTIVITY_HEADER, '1');
	return { ...(init ?? {}), headers };
};

// アイドル失効時の signout を待つ上限。ネットワーク断で画面が残る方が悪いので、
// これを過ぎたら応答を待たずに遷移する(リクエスト自体は keepalive で生き残る)。
const IDLE_SIGNOUT_WAIT_MS = 2000;

/**
 * サーバ側セッションを落とす(#137)。cookie は httpOnly なので FE からは消せない —
 * `GET /auths/signout`(認証不要)に消してもらう。失敗・遅延しても必ず戻る。
 */
const revokeSession = async () => {
	await Promise.race([
		fetch(`${WEBUI_API_BASE_URL}/auths/signout`, {
			method: 'GET',
			headers: { Accept: 'application/json' },
			credentials: 'include',
			// 直後にハード遷移するので、ページが消えてもリクエストは完走させる
			keepalive: true
		}).catch(() => {
			// 不達でも遷移はする
		}),
		new Promise<void>((resolve) => setTimeout(resolve, IDLE_SIGNOUT_WAIT_MS))
	]);
};

/**
 * セッション失効の後始末(フラグ + `/auth` へのハード遷移)。
 *
 * `reason`:
 *   - `'unauthorized'`: サーバが 401/403 を返した = サーバ側は既に死んでいる。即遷移。
 *   - `'idle'`: FE のアイドルタイマが切った(#137)。**遷移の前に signout を叩いて
 *     サーバ側セッションも落とす**。こうしておけば「FE が失効と判断した ⇒ サーバでも
 *     失効している」が構成的に真になり、`/auth` が生きているセッションを見てアプリへ
 *     戻す(共有 PC で前の人の画面が残る)ことが keepalive の発火タイミングの不変条件に
 *     依存しなくなる。
 */
const handleSessionExpired = (reason: 'unauthorized' | 'idle' = 'unauthorized') => {
	if (sessionExpired) {
		return;
	}
	sessionExpired = true;

	try {
		sessionStorage.setItem(SESSION_EXPIRED_FLAG, '1');
	} catch {
		// sessionStorage 不能でも遷移はする(toast が出ないだけ)
	}

	const redirect = () => {
		const currentUrl = `${location.pathname}${location.search}`;
		// ハード遷移でアプリ状態(モジュール状態含む)をリセットする
		location.href = `/auth?redirect=${encodeURIComponent(currentUrl)}`;
	};

	if (reason === 'idle') {
		void revokeSession().then(redirect, redirect);
		return;
	}
	redirect();
};

const verifySession = () => {
	if (verifying || sessionExpired) {
		return;
	}
	verifying = (async () => {
		try {
			const res = await fetch(`${WEBUI_API_BASE_URL}/auths/`, {
				method: 'GET',
				headers: { Accept: 'application/json' }
			});
			if (res.status === 401 || res.status === 403) {
				handleSessionExpired();
			}
		} catch {
			// 不達は unreachable 側の管轄(ここでは何もしない)
		} finally {
			verifying = null;
		}
	})();
};

let healthPoll: ReturnType<typeof setInterval> | null = null;

const startHealthPoll = () => {
	if (healthPoll) {
		return;
	}
	healthPoll = setInterval(async () => {
		try {
			const res = await fetch(`${WEBUI_BASE_URL}/health`, { method: 'GET' });
			if (res.ok) {
				backendUnreachable.set(false);
				if (healthPoll) {
					clearInterval(healthPoll);
					healthPoll = null;
				}
			}
		} catch {
			// まだ不達 — ポーリング継続
		}
	}, 5000);
};

const markUnreachable = () => {
	backendUnreachable.set(true);
	startHealthPoll();
};

/* ---------------------------------------------------------------- *
 * FE 側アイドルタイマ(共有 PC の離席対策)
 * ---------------------------------------------------------------- */

// 経過判定の間隔の上限。TTL そのものではないので設定にはしない。
// 期限そのものは残り時間ちょうどに判定を予約する(scheduleIdleCheck)ので、この粒度は
// 「他タブの操作を取り込み直す間隔」の上限になるだけ(期限を過ぎてからの遅れにはならない)
const IDLE_CHECK_MS = 15000;

let idleTimer: ReturnType<typeof setTimeout> | null = null;
let idleResumeHook: (() => void) | null = null;
// 監視中のアイドルタイムアウト秒数(null = 無期限 / 未ログイン = keepalive も出さない)
let idleTimeoutSeconds: number | null = null;

/**
 * 延命の刻み(ミリ秒) = min(60 秒, TTL/10)。BE(nucllei/utils/auth.py の
 * `session_renew_step_seconds`)と同じ式 — BE は token の年齢がこれを過ぎた操作ヘッダ付きの
 * リクエストで再発行し、FE はサーバの残りが `TTL − 刻み` 以下のときの操作で keepalive を出す。
 * サーバの期限は FE の期限(最後の操作 + TTL)から刻み以内に収まり、操作が続く間の延命は
 * 刻みに 1 本まで。以前は TTL/2 で、残りが半分より多いときの操作がサーバを延ばさず、
 * 席を立つとサーバ側が最大 TTL/2 早く切れていた(リングに残りがあるのにログイン画面へ飛ぶ)。
 */
export const renewStepMs = (ttlMs: number): number => Math.min(60 * 1000, ttlMs / 10);

/**
 * 延命の応答を待つ上限。どこかのタブが keepalive(延命が要るときだけ出す)を出したのに
 * 新しいサーバの期限をまだ知らない間は、サーバの期限を残り時間に使わない(リングは操作の
 * 瞬間に満タンへ戻り、期限ちょうどの操作で応答待ちの間に切ることもしない — 応答が届けば
 * 延びた期限で判断する)。これを過ぎても届かなければ、知っているサーバの期限で判断する
 * (延命が届かなかった = サーバ側は切れている)。
 */
const EXTENSION_PENDING_MS = 10 * 1000;

type IdleSnapshot = {
	remainingMs: number;
	timeoutMs: number;
	/** 残りの計算が切り替わる(延命の応答待ちが終わる)までの時間。無ければ Infinity */
	switchInMs: number;
};

/**
 * 残り時間の計算本体(getIdleRemaining と失効の判定が共有する)。
 *
 * 期限 = 「FE の期限(全タブの最終操作 + TTL)」と「知っているサーバの期限」の早い方。
 * サーバの期限は延命(操作ヘッダ付きの応答)で FE の期限から刻み以内に追いつくので、
 * 普段は FE の期限から刻み以内の値になる。サーバの期限を知らない(古い BE・期限なしの token)
 * ときは FE の期限だけで判断する(従来どおり)。
 */
const idleSnapshot = (): IdleSnapshot | null => {
	if (!idleTimeoutSeconds) {
		return null;
	}
	readShared();
	const now = Date.now();
	const timeoutMs = idleTimeoutSeconds * 1000;
	let deadline = lastInteractionAt + timeoutMs;
	let switchInMs = Infinity;
	if (knownExpiresAt) {
		// keepalive を出したのに、延びた期限をまだ知らない。応答待ちの間はサーバの期限を
		// 使わない(応答が来れば knownExpiresAt が出した時刻 + TTL 近くへ進み、外れる。
		// BE が再発行しない年齢 = 刻み未満だったなら、最初から外れている)。ページ読み込み
		// (FE の期限の起点だが延命はしない)はこれに当たらない
		const awaitingExtension =
			now - extensionRequestedAt < EXTENSION_PENDING_MS &&
			knownExpiresAt < extensionRequestedAt + timeoutMs - renewStepMs(timeoutMs);
		if (awaitingExtension) {
			switchInMs = extensionRequestedAt + EXTENSION_PENDING_MS - now;
		} else {
			deadline = Math.min(deadline, knownExpiresAt);
		}
	}
	return { remainingMs: Math.max(0, deadline - now), timeoutMs, switchInMs };
};

export type IdleRemaining = {
	/** アイドル失効までの残り(ミリ秒、0 以上)。0 = 今まさに切れる */
	remainingMs: number;
	/** アイドルタイムアウトの全長(ミリ秒) = `SESSION_IDLE_TIMEOUT` */
	timeoutMs: number;
};

/**
 * アイドル失効までの残り時間。**失効の判定(startIdleWatch の check)と画面の表示
 * (IdleTimeoutRing)が同じこれを使う** — 表示が 0 になる瞬間と、ログイン画面へ逃がす瞬間を
 * 一致させるため。
 *
 * 残りは「全タブの最終操作時刻 + TTL」と「知っているサーバの期限」の早い方 − 今(idleSnapshot)。
 * 他タブの操作・他タブが知ったサーバの期限は読む直前に localStorage から取り込む(#154)ので、
 * どのタブで計算しても同じ値になり、どこかのタブで操作すれば全タブで(ほぼ)満タンに戻る
 * (サーバの期限は FE の期限から最大で刻みぶん手前 = 実用の TTL で数 %)。
 * 監視していない(無期限・未ログイン・失効後)なら null。
 */
export const getIdleRemaining = (): IdleRemaining | null => {
	const snapshot = idleSnapshot();
	return snapshot ? { remainingMs: snapshot.remainingMs, timeoutMs: snapshot.timeoutMs } : null;
};

/**
 * 「操作したのに、ヘッダを運ぶリクエストが出ていない」ときに出す延命用の 1 本。
 *
 * 人の操作は必ずしもリクエストを生まない — 長い回答をホイールで読む、長文を打ち込む、
 * サイドバーを畳む。その間サーバ側のスライディング期限は延びないので、
 * **画面の前に人が居るのに**次のクリックで 403 → ログイン画面、になってしまう
 * (E2E で実測: wheel だけ 150 秒 → 次のクリックで追い出し。2026-09-05)。
 *
 * 出す条件:
 *   1. **サーバの期限の残りが `TTL − 刻み`(renewStepMs = min(60 秒, TTL/10)。= BE が
 *      再発行する境目)以下**。残りは BE が操作ヘッダ付きの応答に載せる期限
 *      (SESSION_EXPIRES_HEADER)で測る。普通に API を叩いている間は(その応答で期限が
 *      延びるので)ここで弾かれ、追加のリクエストは出ない。操作だけが続く間は刻みに 1 本
 *      (`30m` でも `1d` でも 1 分に 1 本まで。操作が無ければ 0 本)。
 *      **以前は境目が TTL/2 だった(2026-10-20 に変更)。** 残りが半分より多いときの操作は
 *      サーバを延ばさず、そこで席を立つとサーバ側は FE の期限(操作 + TTL)より最大 TTL/2
 *      早く切れた — 戻ってリングに残りがあっても次の操作で 401 → ログイン画面(「ログアウトの
 *      暴発」)。刻みにすれば、サーバの期限は FE の期限から刻み以内に収まる。
 *      **その前は「最後にヘッダを運んでから TTL/2」の自前の時計だった(#154)。** 境目の
 *      直前にヘッダを運ぶと BE は再発行しないのに FE の時計だけ 0 に戻り、次の keepalive が
 *      遅れて期限に間に合わなかった。実際の残りで測れば、境目を過ぎた後の最初の操作で
 *      必ず 1 本出る。
 *      期限が分からない(期限ヘッダを返さない古い BE・まだ 1 本も運べていない)ときは、
 *      最後にヘッダを運んでから TTL/4 で出す(古い BE の半減期の直前に運んでも、TTL/4 後 =
 *      年齢 3TTL/4 で必ず再発行される = 窓は TTL/4)。
 *   2. 呼ばれるのが `noteInteraction()` からだけ = 人が操作した瞬間。
 *      **無操作なら 1 本も出ない** — 「ポーリングでは延命しない」の要はここで守られる。
 *      他タブの操作(共有の最終操作時刻)でも出さない — そのタブ自身が操作の瞬間に出す。
 *   3. 直前の keepalive から刻み以上経っている(連打防止)。普段は 1 本で期限が延びて
 *      1. で弾かれるが、時計のずれ・管理画面での TTL 変更などで BE が再発行しなかったとき、
 *      キーを叩くたびに出続けないように。
 *
 * **アイドル判定のタイマからは呼ばない。** タイマの tick で出すと、操作から最大
 * 半減期ぶん遅れて再発行が走り、サーバの期限が FE のアイドル期限を**追い越す**。
 * すると FE がログイン画面へ逃がしても `/auth` がまだ生きているセッションを見て
 * アプリへ戻してしまい、前の人の画面が復活する(E2E で実測: 操作 t=1.6s →
 * tick で延命 t=26.2s → FE 追い出し t=71.2s → `/auth` が `/` へ戻す。2026-09-05)。
 * 操作した瞬間に出せば `lastActivitySentAt <= lastInteractionAt` が常に成り立ち、
 * サーバのセッションは必ず FE の期限**以前**に死ぬ。
 *
 * #154 の変更後もこの不変条件は崩れない:
 *   - 期限ヘッダで変わるのは「操作の瞬間に**出すかどうか**」だけで、「**いつ**出すか」は
 *     今までどおり操作の瞬間(noteInteraction の同期呼び出し)だけ。期限ヘッダ・共有値を
 *     読んでもサーバの期限は 1 秒も延びない。
 *   - タブ間共有で FE の期限は「全タブの最終操作 + TTL」になる。keepalive はあるタブ自身の
 *     操作の瞬間 t に出て、サーバの期限を t + TTL にする。t はそのまま共有の最終操作時刻に
 *     入る(noteInteraction が先に記録する)ので、t + TTL ≤ 共有の最終操作 + TTL = FE の期限。
 *     他タブの操作を取り込んだだけ(storage の読み直し)では keepalive を出さない。
 *   - 通常リクエストが後から操作ヘッダを運ぶ漏れは、操作から刻みを過ぎたら運ばせない
 *     (withActivityHeader)ので、追い越しても刻み以内。それでも残る分はアイドル失効時の
 *     signout(#137)がサーバ側を構成的に落とす。
 * 境目を TTL/2 → `TTL − 刻み` にしても(2026-10-20)同じ — 変わったのは「出すかどうか」の
 * しきい値と連打防止の間隔だけで、出す瞬間は操作の瞬間のまま。残り時間(idleSnapshot)が
 * サーバの期限を見るのも読むだけで、サーバの期限は延ばさない。
 */
const sendKeepalive = () => {
	if (!idleTimeoutSeconds) {
		return;
	}
	const ttlMs = idleTimeoutSeconds * 1000;
	const stepMs = renewStepMs(ttlMs);
	const now = Date.now();
	if (now - lastKeepaliveAt < stepMs) {
		return;
	}
	readShared();
	if (knownExpiresAt) {
		// サーバの期限は TTL より先には無い(管理画面で TTL を縮めた直後などの古い値を丸める)
		const remaining = Math.min(knownExpiresAt - now, ttlMs);
		if (remaining > ttlMs - stepMs) {
			return;
		}
	} else if (now - lastActivitySentAt < ttlMs / 4) {
		// 期限が分からない — 保守的に TTL/4 間隔。lastActivitySentAt === 0 = このタブでは
		// まだ 1 本も運べていない(開き直した直後など。手元の cookie が既に刻みを過ぎて
		// いる可能性があるので出す)
		return;
	}
	lastKeepaliveAt = now;
	extensionRequestedAt = now;
	writeShared(SHARED_EXTENSION_KEY, extensionRequestedAt);
	void apiFetch(`${WEBUI_API_BASE_URL}/auths/`, {
		method: 'GET',
		headers: { Accept: 'application/json' }
	}).catch(() => {
		// 不達・失効の面倒は apiFetch 側(B2/B3)が見る
	});
};

/**
 * 監視の開始時(ページ読み込み・ログイン直後)に、今のセッションの期限を `$user.expires_at`
 * (`GET /auths/` か signin の応答 = 今の cookie の exp)から知る。
 *
 * - **前のセッションの期限を引きずらない**: localStorage の期限は前のセッション(ログアウト →
 *   再ログイン)のものでありうる。古い(早い)期限のまま残り時間に使うと、ログイン直後に
 *   リングが 0 になって追い出す。新しいセッションの期限は必ずそれより後なので、遅い方を採る。
 *   同じセッションのまま張り直した(管理画面で TTL を変えた等)ときは、`$user` の値の方が
 *   古い — これも遅い方を採れば延命後の期限が残る。
 * - `expires_at` が無い(期限なしで発行された token。TTL を後から有限にした直後)なら、
 *   サーバはまだ期限を持たない — 知っている期限は捨てる(FE の期限だけで判断。次の操作ヘッダ
 *   付きのリクエストで期限付きに再発行され、その応答で知る)。
 * - ページ読み込みは FE の期限の起点になるが、サーバの期限は延ばさない(`GET /auths/` は
 *   再発行しない)。ここで実際の期限を知っておけば、読み込んだだけで席を立ってもリングは
 *   サーバの残りを出す。
 */
const seedServerExpiry = () => {
	const session = get(user);
	if (!session) {
		return;
	}
	const exp = session.expires_at;
	if (typeof exp !== 'number' || !(exp > 0)) {
		knownExpiresAt = 0;
		removeShared(SHARED_EXPIRES_KEY);
		return;
	}
	readShared();
	const seeded = serverSecondsToLocalMs(exp);
	if (seeded > knownExpiresAt) {
		knownExpiresAt = seeded;
		writeShared(SHARED_EXPIRES_KEY, knownExpiresAt);
	}
};

export const stopIdleWatch = () => {
	idleTimeoutSeconds = null;
	if (idleTimer) {
		clearTimeout(idleTimer);
		idleTimer = null;
	}
	if (idleResumeHook) {
		document.removeEventListener('visibilitychange', idleResumeHook);
		window.removeEventListener('focus', idleResumeHook);
		idleResumeHook = null;
	}
};

/**
 * 最後の人の操作から `timeoutSeconds` 経ったらログイン画面へ逃がす。
 *
 * サーバ側(スライディング期限)が切れても、アイドル中のチャットはリクエストを
 * 1 本も出さないので画面は前の人のまま残る。それを塞ぐのがこのタイマ。
 * 失効の後始末(sessionStorage フラグ + ハード遷移 + 到着後のトースト)は
 * 401 経路(B2)と同じ handleSessionExpired に乗せる — 二重には走らない。
 *
 * `timeoutSeconds` は `/api/config` の `session_idle_timeout`
 * (= 既存 `SESSION_IDLE_TIMEOUT`。無期限なら null)。null のときは何もしない。
 */
export const startIdleWatch = (timeoutSeconds?: number | null) => {
	stopIdleWatch();
	if (typeof window === 'undefined' || !timeoutSeconds || timeoutSeconds <= 0) {
		return;
	}

	// 起点はこの瞬間(ページ読み込み自体が人の操作)
	lastInteractionAt = Date.now();
	shareInteraction();
	idleTimeoutSeconds = timeoutSeconds;
	seedServerExpiry();

	const check = () => {
		if (sessionExpired) {
			return;
		}
		// 判定の直前に他タブの最終操作を取り込む(#154 — getIdleRemaining が読み直す)。
		// アイドル扱いは「どのタブでも操作が無い」ときだけ — 下の signout は全タブ共通の
		// cookie を消すので、放置タブの判定だけで作業中のタブを道連れにしない。
		const idle = idleSnapshot();
		if (!idle) {
			return;
		}
		if (idle.remainingMs <= 0) {
			stopIdleWatch();
			handleSessionExpired('idle');
			return;
		}
		scheduleCheck(Math.min(idle.remainingMs, idle.switchInMs));
	};
	// 次の判定は「残り時間ちょうど」(延命の応答待ちが終わるならその時刻)か IDLE_CHECK_MS 後の
	// 早い方。期限ちょうどに判定するので、画面のリングが 0 になる瞬間に切れる(以前の 15 秒間隔
	// だと最大 15 秒遅れていた)。時刻差で判定するのは変わらない — スリープ等でタイマが遅れても、
	// 起きた時点で正しく評価する。
	const scheduleCheck = (waitMs: number) => {
		if (idleTimer) {
			clearTimeout(idleTimer);
		}
		idleTimer = setTimeout(check, Math.min(IDLE_CHECK_MS, waitMs));
	};

	scheduleCheck(timeoutSeconds * 1000);
	// 背面タブ・スリープ復帰ではタイマが飛んでいるので、復帰時点でも評価する
	idleResumeHook = () => {
		if (document.visibilityState === 'visible') {
			check();
		}
	};
	document.addEventListener('visibilitychange', idleResumeHook);
	window.addEventListener('focus', idleResumeHook);
};

export const apiFetch: typeof fetch = async (input, init) => {
	let res: Response;
	try {
		res = await fetch(input, withActivityHeader(input, init));
	} catch (e) {
		// AbortError(呼び出し側の意図的中断)は backend 不達扱いにしない
		if (!(e instanceof DOMException && e.name === 'AbortError')) {
			markUnreachable();
		}
		throw e;
	}

	// リバプロ(Caddy/vite proxy)が backend 死亡時に返すゲートウェイ系エラー
	if ([502, 503, 504].includes(res.status)) {
		markUnreachable();
	} else if (get(backendUnreachable)) {
		backendUnreachable.set(false);
	}

	// 操作ヘッダ付きの応答には今のサーバの期限が載る(keepalive の判断・残り時間に使う)
	noteServerClock(input, res);
	noteServerExpiry(res);

	// ログイン済みセッションでの 401/403 はセッション失効の可能性 — me で裏取りする
	if ((res.status === 401 || res.status === 403) && get(user)) {
		verifySession();
	}

	return res;
};
