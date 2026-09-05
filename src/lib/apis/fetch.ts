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
 *   `JWT_EXPIRES_IN` は「発行からの絶対期限」ではなく「無操作でこの時間が経ったら切れる」。
 *   その判定に要る「人の操作があったか」をここ(全 API が通る唯一の口)で持つ。
 *   - 送信側: 最後の延命以降に操作があったリクエストにだけ ACTIVITY_HEADER を付ける。
 *     チャット画面の 5 秒ポーリングのような機械的なリクエストには付かないので、
 *     タブを開いたまま離席しても延命されない(将来ポーリングが増えても安全側)。
 *   - 画面側: サーバで切れても、アイドル中のチャットはリクエストを 1 本も出さないため
 *     前の人の画面が残り続ける。startIdleWatch() が経過時間を見て自分でログイン画面へ逃がす。
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

let sessionExpired = false;
let verifying: Promise<void> | null = null;

// 最後の延命以降に人の操作があったか(リクエストにヘッダを付けたら降ろす)
let interacted = false;
// 最後に人の操作があった時刻(FE 側アイドルタイマの起点)。
// 残り時間ではなく**時刻の差**で判定するので、スリープ・背面タブでタイマが
// 飛んでも復帰時点で正しく評価できる。
let lastInteractionAt = Date.now();

const noteInteraction = () => {
	interacted = true;
	lastInteractionAt = Date.now();
};

if (typeof window !== 'undefined') {
	// 「操作」= 明確な意図のある入力だけ。mousemove は数えない
	// (通りすがりのカーソル移動で離席中のセッションが延命されてしまうため)。
	for (const type of ['keydown', 'pointerdown', 'click', 'touchstart']) {
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

	const headers = new Headers(
		init?.headers ?? (input instanceof Request ? input.headers : undefined)
	);
	headers.set(ACTIVITY_HEADER, '1');
	return { ...(init ?? {}), headers };
};

const handleSessionExpired = () => {
	if (sessionExpired) {
		return;
	}
	sessionExpired = true;

	try {
		sessionStorage.setItem(SESSION_EXPIRED_FLAG, '1');
	} catch {
		// sessionStorage 不能でも遷移はする(toast が出ないだけ)
	}

	const currentUrl = `${location.pathname}${location.search}`;
	// ハード遷移でアプリ状態(モジュール状態含む)をリセットする
	location.href = `/auth?redirect=${encodeURIComponent(currentUrl)}`;
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

export const stopIdleWatch = () => {
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
 * (= 既存 `JWT_EXPIRES_IN`。無期限なら null)。null のときは何もしない。
 */
export const startIdleWatch = (timeoutSeconds?: number | null) => {
	stopIdleWatch();
	if (typeof window === 'undefined' || !timeoutSeconds || timeoutSeconds <= 0) {
		return;
	}

	// 起点はこの瞬間(ページ読み込み自体が人の操作)
	lastInteractionAt = Date.now();

	const check = () => {
		if (sessionExpired) {
			return;
		}
		if (Date.now() - lastInteractionAt >= timeoutSeconds * 1000) {
			stopIdleWatch();
			handleSessionExpired();
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

	// ログイン済みセッションでの 401/403 はセッション失効の可能性 — me で裏取りする
	if ((res.status === 401 || res.status === 403) && get(user)) {
		verifySession();
	}

	return res;
};
