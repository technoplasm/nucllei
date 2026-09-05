/**
 * アイドルタイムアウト(共有 PC の離席対策)の FE 側回帰テスト。
 *
 * 押さえるのは 2 点:
 *   1. 送信側 — `X-Nucllei-Activity` は**人の操作があったときだけ**付く。
 *      ポーリング(操作なし)には付かない = タブを開いたまま離席しても延命されない。
 *   2. 画面側 — サーバで切れても、アイドル中のチャットはリクエストを 1 本も出さない。
 *      startIdleWatch() が経過時間を見て自分でログイン画面へ逃がす。
 *
 * jsdom は依存に無いので(オフライン配備の repo に増やさない)、
 * モジュール読み込み前に必要なグローバルだけを差し込む。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

type Listener = (...args: unknown[]) => void;

let windowListeners: Record<string, Listener[]>;
let documentListeners: Record<string, Listener[]>;
let fetchMock: ReturnType<typeof vi.fn<Parameters<typeof fetch>, Promise<Response>>>;

const makeTarget = (bag: Record<string, Listener[]>) => ({
	addEventListener: (type: string, fn: Listener) => {
		(bag[type] ??= []).push(fn);
	},
	removeEventListener: (type: string, fn: Listener) => {
		bag[type] = (bag[type] ?? []).filter((f) => f !== fn);
	}
});

const fire = (bag: Record<string, Listener[]>, type: string) => {
	for (const fn of bag[type] ?? []) {
		fn();
	}
};

const loadModule = async () => {
	vi.resetModules();
	windowListeners = {};
	documentListeners = {};

	vi.stubGlobal('window', makeTarget(windowListeners));
	vi.stubGlobal('document', {
		...makeTarget(documentListeners),
		visibilityState: 'visible'
	});
	vi.stubGlobal('location', {
		href: 'http://localhost/c/abc',
		origin: 'http://localhost',
		pathname: '/c/abc',
		search: ''
	});
	const store = new Map<string, string>();
	vi.stubGlobal('sessionStorage', {
		setItem: (k: string, v: string) => store.set(k, v),
		getItem: (k: string) => store.get(k) ?? null,
		removeItem: (k: string) => store.delete(k)
	});

	fetchMock = vi.fn<Parameters<typeof fetch>, Promise<Response>>(
		async () => new Response('{}', { status: 200 })
	);
	vi.stubGlobal('fetch', fetchMock);

	return await import('./fetch');
};

/** 直近の fetch 呼び出しに付いた activity ヘッダの値。 */
const sentActivityHeader = (call: number, headerName: string) => {
	const init = fetchMock.mock.calls[call]?.[1] as RequestInit | undefined;
	return init?.headers ? new Headers(init.headers).get(headerName) : null;
};

beforeEach(() => {
	vi.useFakeTimers();
	vi.setSystemTime(new Date('2026-09-05T00:00:00Z'));
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

describe('activity ヘッダ', () => {
	it('人の操作が無ければ付かない(ポーリングでは延命しない)', async () => {
		const { apiFetch, ACTIVITY_HEADER } = await loadModule();

		await apiFetch('/api/v1/chats/');
		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBeNull();
	});

	it('操作の後の最初の 1 本にだけ付く(次からはまた無操作扱い)', async () => {
		const { apiFetch, ACTIVITY_HEADER } = await loadModule();

		fire(windowListeners, 'keydown');
		await apiFetch('/api/v1/chats/');
		await apiFetch('/api/v1/chats/'); // 続く自動リクエストには付かない

		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBe('1');
		expect(sentActivityHeader(1, ACTIVITY_HEADER)).toBeNull();
	});

	it('既存の headers を潰さない', async () => {
		const { apiFetch, ACTIVITY_HEADER } = await loadModule();

		fire(windowListeners, 'pointerdown');
		await apiFetch('/api/v1/chats/', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' }
		});

		const init = fetchMock.mock.calls[0][1] as RequestInit;
		const headers = new Headers(init.headers);
		expect(headers.get('Content-Type')).toBe('application/json');
		expect(headers.get(ACTIVITY_HEADER)).toBe('1');
		expect(init.method).toBe('POST');
	});

	it('mousemove は操作として数えない', async () => {
		await loadModule();

		// 通りすがりのカーソル移動で離席中のセッションが延命されないこと
		expect(Object.keys(windowListeners).sort()).toEqual([
			'click',
			'keydown',
			'pointerdown',
			'touchstart'
		]);
	});

	it('クロスオリジン宛には付けない(無用な preflight を出さない)', async () => {
		const { apiFetch, ACTIVITY_HEADER } = await loadModule();

		fire(windowListeners, 'click');
		await apiFetch('https://example.invalid/x');
		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBeNull();
	});
});

describe('FE 側アイドルタイマ', () => {
	it('無期限(null)なら何もしない', async () => {
		const { startIdleWatch } = await loadModule();

		startIdleWatch(null);
		vi.advanceTimersByTime(10 * 24 * 60 * 60 * 1000);
		expect(location.href).toBe('http://localhost/c/abc');
	});

	it('無操作で TTL を過ぎたらログイン画面へ逃がす', async () => {
		const { startIdleWatch, SESSION_EXPIRED_FLAG } = await loadModule();

		startIdleWatch(3600);
		vi.advanceTimersByTime(3600 * 1000 + 1000);

		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
		// 到着後のトーストは既存の失効通知(#70 B2)に乗る
		expect(sessionStorage.getItem(SESSION_EXPIRED_FLAG)).toBe('1');
	});

	it('操作があるうちは切れない(スライディング)', async () => {
		const { startIdleWatch } = await loadModule();

		startIdleWatch(3600);
		for (let i = 0; i < 5; i++) {
			vi.advanceTimersByTime(3000 * 1000);
			fire(windowListeners, 'keydown');
		}
		vi.advanceTimersByTime(3000 * 1000);

		expect(location.href).toBe('http://localhost/c/abc');
	});

	it('スリープ・背面タブでタイマが飛んでも、復帰時点の時刻差で判定する', async () => {
		const { startIdleWatch } = await loadModule();

		startIdleWatch(3600);
		// interval を進めずに時計だけ進める(= スリープでタイマが発火しなかった状態)
		vi.setSystemTime(new Date('2026-09-05T05:00:00Z'));
		fire(documentListeners, 'visibilitychange');

		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});

	it('stopIdleWatch でリスナが残らない', async () => {
		const { startIdleWatch, stopIdleWatch } = await loadModule();

		startIdleWatch(3600);
		stopIdleWatch();
		vi.advanceTimersByTime(3600 * 1000 + 1000);

		expect(documentListeners['visibilitychange'] ?? []).toHaveLength(0);
		expect(location.href).toBe('http://localhost/c/abc');
	});
});
