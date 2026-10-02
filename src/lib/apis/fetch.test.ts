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

	it('wheel は操作として数える(長い回答を読んでいる人を追い出さない)', async () => {
		const { apiFetch, ACTIVITY_HEADER } = await loadModule();

		fire(windowListeners, 'wheel');
		await apiFetch('/api/v1/chats/');
		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBe('1');
	});

	it('mousemove は操作として数えない', async () => {
		const { apiFetch, ACTIVITY_HEADER } = await loadModule();

		// 通りすがりのカーソル移動で離席中のセッションが延命されないこと。
		// `wheel` を含めて `mousemove` を外しているのは意図的(上のテストと対)。
		expect(Object.keys(windowListeners).sort()).toEqual([
			'click',
			'keydown',
			'pointerdown',
			'touchstart',
			'wheel'
		]);

		// リスナが無いので、発火させてもヘッダは付かない
		fire(windowListeners, 'mousemove');
		await apiFetch('/api/v1/chats/');
		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBeNull();
	});

	it('クロスオリジン宛には付けない(無用な preflight を出さない)', async () => {
		const { apiFetch, ACTIVITY_HEADER } = await loadModule();

		fire(windowListeners, 'click');
		await apiFetch('https://example.invalid/x');
		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBeNull();
	});
});

describe('noteInteraction(DOM イベントにならない在席 = 音声入力の発話)', () => {
	it('呼ぶと次の 1 本に activity ヘッダが付く', async () => {
		const { apiFetch, noteInteraction, ACTIVITY_HEADER } = await loadModule();

		noteInteraction();
		await apiFetch('/api/v1/audio/transcriptions', { method: 'POST' });
		await apiFetch('/api/v1/audio/transcriptions', { method: 'POST' });

		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBe('1');
		expect(sentActivityHeader(1, ACTIVITY_HEADER)).toBeNull();
	});

	it('呼び続けているうちは TTL を過ぎても切れない', async () => {
		const { startIdleWatch, noteInteraction } = await loadModule();

		startIdleWatch(60);
		for (let i = 0; i < 30; i++) {
			vi.advanceTimersByTime(5000);
			noteInteraction();
		}

		expect(location.href).toBe('http://localhost/c/abc');
	});

	it('呼ばなければ今までどおり切れる(録音バーが開いているだけの無音)', async () => {
		const { startIdleWatch, apiFetch, ACTIVITY_HEADER } = await loadModule();

		startIdleWatch(60);
		// 操作を伴わないリクエストが出ていても延命されない
		vi.advanceTimersByTime(30000);
		await apiFetch('/api/v1/audio/transcriptions', { method: 'POST' });
		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBeNull();
		vi.advanceTimersByTime(31000);

		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});

	it('呼ぶのをやめてから TTL が経てば切れる(喋り終えて離席)', async () => {
		const { startIdleWatch, noteInteraction } = await loadModule();

		startIdleWatch(60);
		vi.advanceTimersByTime(50000);
		noteInteraction();
		vi.advanceTimersByTime(50000);
		expect(location.href).toBe('http://localhost/c/abc');
		// 判定は 15 秒刻み(IDLE_CHECK_MS)— 最後の記録から 60 秒を過ぎた次の tick で切れる
		vi.advanceTimersByTime(20000);

		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
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

	it('操作はあるがリクエストが出ないときは、延命の 1 本を出す', async () => {
		// スクロールして読む・長文を打つ = 操作はあるがリクエストは 0 本。
		// これを放置するとサーバ側の期限だけ切れ、次のクリックで追い出される。
		// このタブではまだヘッダを 1 本も運べていない(= 半減期の条件は自動的に成立)。
		const { startIdleWatch, ACTIVITY_HEADER } = await loadModule();

		startIdleWatch(3600);
		fire(windowListeners, 'wheel');

		expect(fetchMock.mock.calls).toHaveLength(1);
		expect(String(fetchMock.mock.calls[0][0])).toContain('/auths/');
		expect(sentActivityHeader(0, ACTIVITY_HEADER)).toBe('1');
	});

	it('無操作のときは延命の 1 本も出さない(ポーリングでは延命しない)', async () => {
		const { startIdleWatch } = await loadModule();

		startIdleWatch(3600);
		vi.advanceTimersByTime(15000 * 10);

		expect(fetchMock.mock.calls).toHaveLength(0);
	});

	it('keepalive はタイマの tick では出さない(サーバ期限が FE の期限を追い越さない)', async () => {
		// tick で出すと、操作から最大半減期ぶん遅れて再発行が走り、サーバの期限が
		// FE のアイドル期限を追い越す。すると FE が /auth へ逃がしても、まだ生きて
		// いるセッションを見て /auth がアプリへ戻し、前の人の画面が復活する。
		const { startIdleWatch } = await loadModule();

		startIdleWatch(60);
		fire(windowListeners, 'wheel'); // 操作した瞬間に 1 本(まだ運べていないため)
		expect(fetchMock.mock.calls).toHaveLength(1);

		// 以後は無操作。tick がいくら回っても増えない(= 追い越しが起きない)
		vi.advanceTimersByTime(45000);
		expect(fetchMock.mock.calls).toHaveLength(1);
	});

	it('半減期を過ぎるまでは keepalive を出さない(操作し続けても増やさない)', async () => {
		const { startIdleWatch, apiFetch } = await loadModule();

		// 通常の API 呼び出しがヘッダを運んだ = ここが起点
		fire(windowListeners, 'click');
		await apiFetch('/api/v1/chats/');
		startIdleWatch(3600);

		// 15 秒ごとにホイールを回し続けるが、まだ半減期(1800 秒)に届かない
		for (let i = 0; i < 100; i++) {
			vi.advanceTimersByTime(15000);
			fire(windowListeners, 'wheel');
		}

		expect(fetchMock.mock.calls).toHaveLength(1); // 最初の 1 本だけ
	});

	it('操作が続いても keepalive は半減期ごとに 1 本(操作のたびには出さない)', async () => {
		const { startIdleWatch, apiFetch } = await loadModule();

		fire(windowListeners, 'click');
		await apiFetch('/api/v1/chats/');
		startIdleWatch(3600);

		// 3 時間ぶん、15 秒ごとにホイールを回し続ける
		for (let i = 0; i < 720; i++) {
			vi.advanceTimersByTime(15000);
			fire(windowListeners, 'wheel');
		}

		// 10800 秒 / 半減期 1800 秒 = 6 本。操作のたびに出すと 720 本になってしまう
		const keepalives = fetchMock.mock.calls.slice(1);
		expect(keepalives).toHaveLength(6);
		for (const call of keepalives) {
			expect(String(call[0])).toContain('/auths/');
		}
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
