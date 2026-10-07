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
 *     **操作した瞬間に、サーバの期限の残りが半分を切っていれば 1 本だけ**延命のリクエスト
 *     (sendKeepalive)を出す。残りはサーバが返す期限(SESSION_EXPIRES_HEADER)で測る(#154)。
 *     操作が無ければ 1 本も出ない。
 *   - 複数タブ(#154): 最終操作時刻とサーバの期限は localStorage でタブ間に共有する。
 *     cookie は全タブ共通なので、アイドル判定は「**どのタブでも**操作が無い」ときだけ —
 *     放置タブのアイドル判定が signout で作業中のタブを道連れにしない。
 *   - 「人の操作」には、マイクの音声入力で発話を検出している間も含める(noteInteraction を
 *     VoiceRecording が呼ぶ)。録音バーが開いているだけの無音は含めない。
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

let sessionExpired = false;
let verifying: Promise<void> | null = null;

// 最後の延命以降に人の操作があったか(リクエストにヘッダを付けたら降ろす)
let interacted = false;
// 最後に活動ヘッダを**実際に運べた**時刻(keepalive でも通常の API 呼び出しでもよい)。
// サーバの期限が分からない(期限ヘッダを返さない古い BE)ときの keepalive の判定にだけ使う。
// `0` = このタブではまだ 1 本も運べていない。
let lastActivitySentAt = 0;
// 最後に keepalive を出した時刻(連打防止 — sendKeepalive の説明)
let lastKeepaliveAt = 0;
// 最後に知ったサーバの期限(このブラウザの時計に直した epoch ミリ秒。`0` = 不明)。
// 他タブが知った値は readShared() で取り込む。
let knownExpiresAt = 0;
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

/** 他タブの最終操作時刻・サーバの期限を取り込む。 */
const readShared = () => {
	// 未来の時刻は信用しない(同じ時計なので本来起きない)
	const interaction = Math.min(readSharedNumber(SHARED_INTERACTION_KEY), Date.now());
	if (interaction > lastInteractionAt) {
		lastInteractionAt = interaction;
	}
	// 期限は「最後に知った値」をそのまま採る(max にしない)。cookie は 1 つなので最新の
	// 応答が正。古い値が残っていても、期限は延命・再ログインで延びる一方なので
	// 古い値は短め = keepalive を早めに出す安全側にしか倒れない。
	const expires = readSharedNumber(SHARED_EXPIRES_KEY);
	if (expires) {
		knownExpiresAt = expires;
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
 * 応答の期限ヘッダを覚える。サーバの時計とずれていても残り時間が正しくなるよう、
 * 応答の `Date`(あれば)との差を手元の時計に足し直す。
 */
const noteServerExpiry = (res: Response) => {
	const raw = res.headers?.get?.(SESSION_EXPIRES_HEADER);
	const exp = raw ? Number(raw) : NaN;
	if (!Number.isFinite(exp) || exp <= 0) {
		return;
	}
	const serverNow = Date.parse(res.headers.get('Date') ?? '');
	const remainingMs = exp * 1000 - (Number.isFinite(serverNow) ? serverNow : Date.now());
	knownExpiresAt = Date.now() + remainingMs;
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

// 経過判定の間隔。TTL そのものではないので設定にはしない
// (時刻差で判定するため、この粒度が遅れの上限になるだけ)
const IDLE_CHECK_MS = 15000;

let idleTimer: ReturnType<typeof setInterval> | null = null;
let idleResumeHook: (() => void) | null = null;
// 監視中のアイドルタイムアウト秒数(null = 無期限 / 未ログイン = keepalive も出さない)
let idleTimeoutSeconds: number | null = null;

/**
 * 「操作したのに、ヘッダを運ぶリクエストが出ていない」ときに出す延命用の 1 本。
 *
 * 人の操作は必ずしもリクエストを生まない — 長い回答をホイールで読む、長文を打ち込む、
 * サイドバーを畳む。その間サーバ側のスライディング期限は延びないので、
 * **画面の前に人が居るのに**次のクリックで 403 → ログイン画面、になってしまう
 * (E2E で実測: wheel だけ 150 秒 → 次のクリックで追い出し。2026-09-05)。
 *
 * 出す条件:
 *   1. **サーバの期限の残りが `idleTimeoutSeconds / 2`(= BE が再発行する境目)以下**。
 *      残りは BE が操作ヘッダ付きの応答に載せる期限(SESSION_EXPIRES_HEADER)で測る。
 *      普通に API を叩いている間は(その応答で期限が延びるので)ここで弾かれ、追加の
 *      リクエストは出ない。`1d` なら 12 時間に 1 本、`30m` なら 15 分に 1 本程度
 *      (院内の多人数配備では無視できない — ken 2026-09-05)。
 *      **以前は「最後にヘッダを運んでから TTL/2」の自前の時計だった(#154)。** 年齢 TTL/2 の
 *      直前にヘッダを運ぶと BE は再発行しないのに FE の時計だけ 0 に戻り、次の keepalive が
 *      年齢 TTL 近くまで出ない — 延命の窓が数秒しかなく、操作中でも切れていた。
 *      実際の残りで測れば、残りが半分を切った後の最初の操作で必ず 1 本出る
 *      (窓は常に TTL/2 ある)。
 *      期限が分からない(期限ヘッダを返さない古い BE・まだ 1 本も運べていない)ときは、
 *      最後にヘッダを運んでから TTL/4 で出す(半減期の直前に運んでも、TTL/4 後 = 年齢
 *      3TTL/4 で必ず再発行される = 窓は TTL/4)。
 *   2. 呼ばれるのが `noteInteraction()` からだけ = 人が操作した瞬間。
 *      **無操作なら 1 本も出ない** — 「ポーリングでは延命しない」の要はここで守られる。
 *      他タブの操作(共有の最終操作時刻)でも出さない — そのタブ自身が操作の瞬間に出す。
 *   3. 直前の keepalive から TTL/8 以上経っている(連打防止)。普段は 1 本で期限が延びて
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
 *   - それでも漏れがあれば(通常リクエストが後から操作ヘッダを運ぶ等)、アイドル失効時の
 *     signout(#137)がサーバ側を構成的に落とす。
 */
const sendKeepalive = () => {
	if (!idleTimeoutSeconds) {
		return;
	}
	const ttlMs = idleTimeoutSeconds * 1000;
	const now = Date.now();
	if (now - lastKeepaliveAt < ttlMs / 8) {
		return;
	}
	readShared();
	if (knownExpiresAt) {
		// サーバの期限は TTL より先には無い(管理画面で TTL を縮めた直後などの古い値を丸める)
		const remaining = Math.min(knownExpiresAt - now, ttlMs);
		if (remaining > ttlMs / 2) {
			return;
		}
	} else if (now - lastActivitySentAt < ttlMs / 4) {
		// 期限が分からない — 保守的に TTL/4 間隔。lastActivitySentAt === 0 = このタブでは
		// まだ 1 本も運べていない(開き直した直後など。手元の cookie が既に半分を過ぎて
		// いる可能性があるので出す)
		return;
	}
	lastKeepaliveAt = now;
	void apiFetch(`${WEBUI_API_BASE_URL}/auths/`, {
		method: 'GET',
		headers: { Accept: 'application/json' }
	}).catch(() => {
		// 不達・失効の面倒は apiFetch 側(B2/B3)が見る
	});
};

export const stopIdleWatch = () => {
	idleTimeoutSeconds = null;
	if (idleTimer) {
		clearInterval(idleTimer);
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

	const check = () => {
		if (sessionExpired) {
			return;
		}
		// 判定の直前に他タブの最終操作を取り込む(#154)。アイドル扱いは「どのタブでも
		// 操作が無い」ときだけ — 下の signout は全タブ共通の cookie を消すので、
		// 放置タブの判定だけで作業中のタブを道連れにしない。
		readShared();
		if (Date.now() - lastInteractionAt >= timeoutSeconds * 1000) {
			stopIdleWatch();
			handleSessionExpired('idle');
		}
	};

	idleTimer = setInterval(check, IDLE_CHECK_MS);
	// 背面タブ・スリープ復帰では interval が飛んでいるので、復帰時点でも評価する
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

	// 操作ヘッダ付きの応答には今のサーバの期限が載る(keepalive の判断に使う)
	noteServerExpiry(res);

	// ログイン済みセッションでの 401/403 はセッション失効の可能性 — me で裏取りする
	if ((res.status === 401 || res.status === 403) && get(user)) {
		verifySession();
	}

	return res;
};
