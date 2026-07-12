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
 */
import { get } from 'svelte/store';

import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
import { user, backendUnreachable } from '$lib/stores';

export const SESSION_EXPIRED_FLAG = 'llens:session-expired';

let sessionExpired = false;
let verifying: Promise<void> | null = null;

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

export const apiFetch: typeof fetch = async (input, init) => {
	let res: Response;
	try {
		res = await fetch(input, init);
	} catch (e) {
		markUnreachable();
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
