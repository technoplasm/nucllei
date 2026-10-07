/**
 * アイドルタイムアウト(共有 PC の離席対策)の FE 側回帰テスト。
 *
 * 押さえるのは 2 点:
 *   1. 送信側 — `X-Nucllei-Activity` は**人の操作があったときだけ**付く。
 *      ポーリング(操作なし)には付かない = タブを開いたまま離席しても延命されない。
 *   2. 画面側 — サーバで切れても、アイドル中のチャットはリクエストを 1 本も出さない。
 *      startIdleWatch() が経過時間を見て自分でログイン画面へ逃がす。
 *   3. 延命の境界(#154)— keepalive はサーバが返す期限の実際の残りで判断する。
 *      複数タブは最終操作時刻を共有し、どのタブでも操作が無いときだけ切る。
 *
 * jsdom は依存に無いので(オフライン配備の repo に増やさない)、
 * モジュール読み込み前に必要なグローバルだけを差し込む。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

type Listener = (...args: unknown[]) => void;

let windowListeners: Record<string, Listener[]>;
let documentListeners: Record<string, Listener[]>;
let fetchMock: ReturnType<typeof vi.fn<Parameters<typeof fetch>, Promise<Response>>>;
// タブ間で共有される localStorage(同じブラウザの別タブ = 同じ Map)
let localStore: Map<string, string>;

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

/**
 * モジュールを読み込む(= タブを 1 つ開く)。
 * `secondTab: true` なら localStorage と fetch(= cookie を持つサーバ)を前のタブと共有したまま、
 * 別のモジュール状態・別の window リスナでもう 1 つ開く。前のタブのリスナは `windowListeners`
 * を読み込み前に退避しておけば発火できる。
 */
const loadModule = async ({ secondTab = false } = {}) => {
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

	if (!secondTab) {
		localStore = new Map();
		fetchMock = vi.fn<Parameters<typeof fetch>, Promise<Response>>(
			async () => new Response('{}', { status: 200 })
		);
	}
	vi.stubGlobal('localStorage', {
		setItem: (k: string, v: string) => localStore.set(k, v),
		getItem: (k: string) => localStore.get(k) ?? null,
		removeItem: (k: string) => localStore.delete(k)
	});
	vi.stubGlobal('fetch', fetchMock);

	return await import('./fetch');
};

/**
 * アイドル失効は signout の応答を待ってから遷移する(#137)ので、promise の
 * 決着(マイクロタスク)を流してから `location.href` を見る。
 */
const settle = async () => {
	await vi.advanceTimersByTimeAsync(0);
};

/** signout を叩いた回数(`/auths/signout` 宛の fetch)。 */
const signoutCalls = () =>
	fetchMock.mock.calls.filter((call) => String(call[0]).endsWith('/auths/signout'));

/** 直近の fetch 呼び出しに付いた activity ヘッダの値。 */
const sentActivityHeader = (call: number, headerName: string) => {
	const init = fetchMock.mock.calls[call]?.[1] as RequestInit | undefined;
	return init?.headers ? new Headers(init.headers).get(headerName) : null;
};

/**
 * cookie セッションを持つ BE の振る舞い(nucllei/utils/auth.py)を模す:
 *   - 期限(exp、epoch 秒)を過ぎたリクエストは 401(延命しない)
 *   - 操作ヘッダ付きで、残りが TTL/2 以下なら再発行(exp = 今 + TTL)
 *   - 操作ヘッダ付きの応答には今の exp を期限ヘッダで返す(`expiresHeader: false` = 古い BE)
 *   - signout で cookie が消える
 * `expiredAt` は「操作中なのにサーバ側が切れた」瞬間(切れなければ null)。
 */
const fakeBackend = (ttlSeconds: number, { expiresHeader = true } = {}) => {
	const state = {
		exp: Math.floor(Date.now() / 1000) + ttlSeconds,
		signedOut: false,
		expiredAt: null as number | null
	};
	fetchMock.mockImplementation(async (input, init) => {
		const now = Date.now() / 1000;
		if (String(input).endsWith('/auths/signout')) {
			state.signedOut = true;
			return new Response('{}', { status: 200 });
		}
		if (state.signedOut || now >= state.exp) {
			state.expiredAt ??= now;
			return new Response('{}', { status: 401 });
		}
		const headers = new Headers();
		if (init?.headers && new Headers(init.headers).get('X-Nucllei-Activity')) {
			if (state.exp - now <= ttlSeconds / 2) {
				state.exp = Math.floor(now) + ttlSeconds;
			}
			if (expiresHeader) {
				headers.set('X-Nucllei-Session-Expires', String(state.exp));
			}
		}
		return new Response('{}', { status: 200, headers });
	});
	return state;
};

/** `/auths/` 宛の keepalive(signout 以外)の本数。 */
const keepaliveCalls = () =>
	fetchMock.mock.calls.filter((call) => String(call[0]).endsWith('/auths/'));

/** サーバ時計の「今」の残り(秒)。 */
const serverRemaining = (state: { exp: number }) => state.exp - Date.now() / 1000;

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
		await settle();

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
		await settle();

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
		await settle();

		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
		// 到着後のトーストは既存の失効通知(#70 B2)に乗る
		expect(sessionStorage.getItem(SESSION_EXPIRED_FLAG)).toBe('1');
	});

	it('アイドル失効では /auth へ行く前に signout でサーバ側セッションも落とす(#137)', async () => {
		// FE が失効と判断したのにサーバ側のセッションが生きていると、/auth が生きた
		// セッションを見てアプリへ戻し、共有 PC で前の人の画面が復活する。
		// signout を先に叩けば「FE が切った ⇒ サーバでも切れている」が構成的に真になる。
		const { startIdleWatch } = await loadModule();

		startIdleWatch(60);
		vi.advanceTimersByTime(61 * 1000);

		// 遷移は signout の応答を待ってから
		expect(signoutCalls()).toHaveLength(1);
		const init = signoutCalls()[0][1] as RequestInit;
		expect(init.credentials).toBe('include');
		expect(init.keepalive).toBe(true);
		expect(location.href).toBe('http://localhost/c/abc');

		await settle();
		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});

	it('signout が失敗しても必ず遷移する(ネットワーク断で画面が残る方が悪い)', async () => {
		const { startIdleWatch } = await loadModule();
		fetchMock.mockImplementation(async () => {
			throw new TypeError('Failed to fetch');
		});

		startIdleWatch(60);
		vi.advanceTimersByTime(61 * 1000);
		await settle();

		expect(signoutCalls()).toHaveLength(1);
		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});

	it('signout が返ってこなくても上限時間で遷移する', async () => {
		const { startIdleWatch } = await loadModule();
		fetchMock.mockImplementation(() => new Promise<Response>(() => {}));

		startIdleWatch(60);
		vi.advanceTimersByTime(61 * 1000);
		await settle();
		expect(location.href).toBe('http://localhost/c/abc'); // まだ待っている

		await vi.advanceTimersByTimeAsync(2000);
		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});

	it('401 起因の失効では signout を叩かない(サーバ側は既に死んでいる)', async () => {
		const { apiFetch } = await loadModule();
		const { user } = await import('$lib/stores');
		user.set({ id: '1' } as never);
		fetchMock.mockImplementation(async () => new Response('{}', { status: 401 }));

		await apiFetch('/api/v1/chats/');
		await settle();

		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
		expect(signoutCalls()).toHaveLength(0);
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
		await settle();

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
		fakeBackend(3600);

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
		const backend = fakeBackend(3600);

		fire(windowListeners, 'click');
		await apiFetch('/api/v1/chats/');
		startIdleWatch(3600);

		// 3 時間ぶん、15 秒ごとにホイールを回し続ける(keepalive の応答 = 新しい期限を受け取る)
		for (let i = 0; i < 720; i++) {
			await vi.advanceTimersByTimeAsync(15000);
			fire(windowListeners, 'wheel');
		}

		// 10800 秒 / 半減期 1800 秒 = 6 本。操作のたびに出すと 720 本になってしまう
		const keepalives = fetchMock.mock.calls.slice(1);
		expect(keepalives).toHaveLength(6);
		for (const call of keepalives) {
			expect(String(call[0])).toContain('/auths/');
		}
		expect(backend.expiredAt).toBeNull();
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

describe('延命の境界(#154)', () => {
	/**
	 * staging で起きた形: TTL 20 分。年齢 TTL/2 の直前(9:55)に操作ヘッダ付きの API が出る
	 * (BE は残りが半分を超えているので再発行しない)。その後は API を出さない操作
	 * (ホイールで読む)だけが 20 秒おきに続く。
	 * 旧実装は「最後にヘッダを送ってから TTL/2」の時計を 9:55 で 0 に戻すので、次の
	 * keepalive は 19:55 以降 = 20:00 の操作で、その時点でサーバ側は切れていた。
	 */
	const ttl = 1200;
	const scenario = async (expiresHeader: boolean) => {
		const { startIdleWatch, apiFetch } = await loadModule();
		const backend = fakeBackend(ttl, { expiresHeader });
		startIdleWatch(ttl);

		// ログイン直後の操作 + API(ヘッダを 1 本運ぶ)
		fire(windowListeners, 'click');
		await apiFetch('/api/v1/chats/');
		await settle();

		await vi.advanceTimersByTimeAsync(595 * 1000);
		fire(windowListeners, 'click');
		await apiFetch('/api/v1/chats/'); // 年齢 595 秒 = 半減期の 5 秒前
		await settle();
		expect(backend.exp - Math.floor(Date.now() / 1000)).toBe(ttl - 595); // 再発行されていない

		// 40 分間、リクエストを生まない操作だけが続く(年齢 600, 620, … 秒。旧実装の
		// 延命の窓 [1195, 1200) には操作が入らない — 窓が 5 秒しかないこと自体が不具合)
		await vi.advanceTimersByTimeAsync(5 * 1000);
		fire(windowListeners, 'wheel');
		await settle();
		for (let i = 0; i < 120; i++) {
			await vi.advanceTimersByTimeAsync(20 * 1000);
			fire(windowListeners, 'wheel');
			await settle();
			expect(serverRemaining(backend)).toBeGreaterThan(0);
		}
		return backend;
	};

	it('(a) 半減期の直前にヘッダを運んでも、残りが半分を切った後の操作で延命して切れない', async () => {
		const backend = await scenario(true);

		expect(backend.expiredAt).toBeNull();
		expect(location.href).toBe('http://localhost/c/abc');
		// 残りが半分を切った最初の操作(年齢 600 秒)で 1 本出ている
		expect(keepaliveCalls().length).toBeGreaterThanOrEqual(2);
	});

	it('(a) keepalive は半減期ごと程度で、操作のたびには出さない', async () => {
		await scenario(true);

		// 最初の 1 本(期限不明)+ 40 分 / 半減期 10 分 ≒ 4〜5 本。120 回の操作ぶんではない
		expect(keepaliveCalls().length).toBeLessThanOrEqual(6);
	});

	it('(d) 期限ヘッダを返さない古い BE では TTL/4 間隔にフォールバックして切れない', async () => {
		const backend = await scenario(false);

		expect(backend.expiredAt).toBeNull();
		expect(location.href).toBe('http://localhost/c/abc');
		// TTL/4 = 5 分間隔: 40 分で 8〜9 本程度(操作のたびではない)
		expect(keepaliveCalls().length).toBeLessThanOrEqual(12);
	});

	it('BE が再発行しなかった keepalive の後、操作のたびに出し続けない(連打防止)', async () => {
		const { startIdleWatch, noteInteraction } = await loadModule();
		// 再発行しない BE(時計のずれ・TTL 変更の直後などの模擬): 期限は常に残り 1 分
		fetchMock.mockImplementation(
			async () =>
				new Response('{}', {
					status: 200,
					headers: {
						'X-Nucllei-Session-Expires': String(Math.floor(Date.now() / 1000) + 60)
					}
				})
		);
		startIdleWatch(1200);

		for (let i = 0; i < 60; i++) {
			await vi.advanceTimersByTimeAsync(1000);
			noteInteraction();
		}
		// 60 秒間に操作 60 回 → TTL/8 = 150 秒の間隔なので 1 本だけ
		expect(keepaliveCalls()).toHaveLength(1);
	});

	it('期限が無い設定(SESSION_IDLE_TIMEOUT = -1)では keepalive も共有の書き込みも無い', async () => {
		const { startIdleWatch, apiFetch } = await loadModule();

		startIdleWatch(null);
		for (let i = 0; i < 10; i++) {
			vi.advanceTimersByTime(3600 * 1000);
			fire(windowListeners, 'wheel');
		}
		await apiFetch('/api/v1/chats/');

		expect(fetchMock.mock.calls).toHaveLength(1); // 自分で出した 1 本だけ
		expect(location.href).toBe('http://localhost/c/abc');
	});
});

describe('複数タブ(#154)', () => {
	it('(b) 放置タブ B が TTL を過ぎても、A で操作中なら B は signout しない', async () => {
		const tabA = await loadModule();
		const backend = fakeBackend(60);
		tabA.startIdleWatch(60);
		const listenersA = windowListeners;

		const tabB = await loadModule({ secondTab: true });
		tabB.startIdleWatch(60);

		// A だけで 10 秒おきに操作し続ける(B は一度も触らない)。5 分 = TTL の 5 倍
		for (let i = 0; i < 30; i++) {
			await vi.advanceTimersByTimeAsync(10 * 1000);
			fire(listenersA, 'keydown');
			await settle();
		}
		// B の背面復帰(visibilitychange)でも切れない
		fire(documentListeners, 'visibilitychange');
		await settle();

		expect(signoutCalls()).toHaveLength(0);
		expect(location.href).toBe('http://localhost/c/abc');
		expect(backend.expiredAt).toBeNull();
	});

	it('(b) signout の直前に共有の最終操作時刻を読み直す(B のタイマの tick 直前に A で操作)', async () => {
		const tabA = await loadModule();
		tabA.startIdleWatch(60);
		const listenersA = windowListeners;

		const tabB = await loadModule({ secondTab: true });
		tabB.startIdleWatch(60);

		// B の起点から 59 秒後に A で操作 → B の 60 秒目の判定時点では A の操作から 1 秒
		vi.advanceTimersByTime(59 * 1000);
		fire(listenersA, 'click');
		vi.advanceTimersByTime(16 * 1000);
		await settle();

		expect(signoutCalls()).toHaveLength(0);
		expect(location.href).toBe('http://localhost/c/abc');
	});

	it('(c) どのタブでも操作が無ければ、従来どおりアイドルで切れる', async () => {
		const tabA = await loadModule();
		tabA.startIdleWatch(60);
		const tabB = await loadModule({ secondTab: true });
		tabB.startIdleWatch(60);

		vi.advanceTimersByTime(76 * 1000);
		await settle();

		expect(signoutCalls().length).toBeGreaterThanOrEqual(1);
		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});

	it('(c) A の操作が止まれば、そこから TTL で B も切れる', async () => {
		const tabA = await loadModule();
		tabA.startIdleWatch(60);
		const listenersA = windowListeners;
		const tabB = await loadModule({ secondTab: true });
		tabB.startIdleWatch(60);

		vi.advanceTimersByTime(50 * 1000);
		fire(listenersA, 'click'); // 最後の操作(t=50)
		vi.advanceTimersByTime(55 * 1000);
		expect(signoutCalls()).toHaveLength(0);

		vi.advanceTimersByTime(20 * 1000); // t=125 > 50 + 60
		await settle();
		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});

	it('サーバの期限もタブ間で共有する(B が知った延命を A の判断に使う)', async () => {
		const tabA = await loadModule();
		const backend = fakeBackend(1200);
		tabA.startIdleWatch(1200);
		const listenersA = windowListeners;
		fire(listenersA, 'click'); // A: 期限不明なので 1 本(期限を知る)
		await settle();

		const tabB = await loadModule({ secondTab: true });
		tabB.startIdleWatch(1200);

		// 半減期を過ぎてから B で操作 → B が延命し、新しい期限を共有に書く
		await vi.advanceTimersByTimeAsync(700 * 1000);
		fire(windowListeners, 'click');
		await settle();
		const before = keepaliveCalls().length;
		expect(serverRemaining(backend)).toBeGreaterThan(1100);

		// A は古い期限(残り 500 秒)ではなく共有の期限を見るので、出さない
		fire(listenersA, 'click');
		await settle();
		expect(keepaliveCalls()).toHaveLength(before);
	});

	it('localStorage が例外を投げても、タブ単独の判定で動く', async () => {
		const { startIdleWatch } = await loadModule();
		vi.stubGlobal('localStorage', {
			getItem: () => {
				throw new DOMException('denied', 'SecurityError');
			},
			setItem: () => {
				throw new DOMException('denied', 'SecurityError');
			}
		});
		startIdleWatch(60);
		fire(windowListeners, 'keydown');
		vi.advanceTimersByTime(30 * 1000);
		expect(location.href).toBe('http://localhost/c/abc');

		vi.advanceTimersByTime(46 * 1000);
		await settle();
		expect(location.href).toBe('/auth?redirect=%2Fc%2Fabc');
	});
});
