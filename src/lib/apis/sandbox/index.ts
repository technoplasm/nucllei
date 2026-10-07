import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';

// #150: sandbox のファイルブラウザ(nucllei/routers/sandbox_files.py)。
// パスは jail の絶対パス(/workspace/… ・/ext/public/… ・/ext/groups/<g>/…)。
// 失敗は SandboxFilesError(status と detail)で投げる — 画面が 409(外で変更された /
// 既に在る)・413(大きすぎる。detail.limit に上限)・507(容量が一杯)を言い分けるため。

const BASE = `${WEBUI_API_BASE_URL}/sandbox/files`;

export type SandboxPlace = {
	path: string;
	kind: 'workspace' | 'public' | 'group';
	rw: boolean;
};

export type SandboxEntry = {
	name: string;
	path: string;
	type: 'dir' | 'file' | 'symlink' | 'other';
	size: number;
	mtime: number | null;
	// 古い sandbox は返さない(null)。imports/ exports/ trash/ の直下は max(mtime, ctime) + N 日で消える
	ctime?: number | null;
};

export type SandboxListing = {
	path: string;
	rw: boolean;
	exists: boolean;
	is_dir: boolean;
	truncated: boolean;
	entries: SandboxEntry[];
	cache_ttl_days: number | null;
};

export type SandboxFileContent = {
	path: string;
	name: string;
	size: number;
	mtime: number | null;
	rw: boolean;
	// 平文(UTF-8・NUL 無し・上限内)なら中身、それ以外は null(ダウンロードのみ)
	text: string | null;
	reason: 'too_large' | 'binary' | null;
};

export type SandboxUsage = {
	used_bytes: number;
	total_bytes: number;
	quota_gb: number;
};

export class SandboxFilesError extends Error {
	status: number;
	detail: unknown;

	constructor(status: number, detail: unknown) {
		const message =
			typeof detail === 'string'
				? detail
				: ((detail as { message?: string } | null)?.message ?? `HTTP ${status}`);
		super(message);
		this.status = status;
		this.detail = detail;
	}
}

const request = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
	let res: Response;
	try {
		res = await apiFetch(`${BASE}${path}`, {
			...init,
			headers: { Accept: 'application/json', ...(init.headers ?? {}) }
		});
	} catch (e) {
		throw new SandboxFilesError(0, `${e}`);
	}
	if (!res.ok) {
		const body = await res.json().catch(() => null);
		throw new SandboxFilesError(res.status, body?.detail ?? res.statusText);
	}
	return res.json();
};

const postJson = <T>(path: string, body: unknown) =>
	request<T>(path, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});

export type SandboxPlaces = {
	places: SandboxPlace[];
	// テキストの上限(エディタで開く・保存。SANDBOX_TEXT_MAX_SIZE_MB)
	text_limit_bytes: number;
	// アップロードの上限(sandbox の SANDBOX_FILE_MAX_SIZE_MB)。サーバーが読めていなければ null —
	// そのときは事前チェックをせず sandbox の 413 で知る
	upload_limit_bytes: number | null;
	// アップロードの権限(グループの「ファイルアップロードを許可」= chat.file_upload)
	can_upload: boolean;
};

export const getSandboxPlaces = () => request<SandboxPlaces>('/places');

export const getSandboxUsage = () => request<SandboxUsage>('/usage');

export const listSandboxDir = (path: string) => postJson<SandboxListing>('/list', { path });

export const readSandboxFile = (path: string) => postJson<SandboxFileContent>('/read', { path });

export const writeSandboxFile = (
	path: string,
	content: string,
	options: { expectedMtime?: number | null; exclusive?: boolean } = {}
) =>
	postJson<{ ok: boolean; path: string; size: number; mtime: number | null }>('/write', {
		path,
		content,
		expected_mtime: options.expectedMtime ?? null,
		exclusive: options.exclusive ?? false
	});

// サーバーは本文を sandbox へストリーミングで中継する(1 GB 級でも可。上限は upload_limit_bytes)
export const uploadSandboxFile = (dir: string, file: File, overwrite = false) => {
	const data = new FormData();
	data.append('dir', dir);
	data.append('overwrite', overwrite ? 'true' : 'false');
	data.append('file', file);
	return request<{ ok: boolean; path: string; size: number }>('/upload', {
		method: 'POST',
		body: data
	});
};

// /workspace の中(trash/ の外)はサーバーが /workspace/trash/ の直下へ移す(同名は -2, -3…)—
// trashed: true と移した先の path を返す。permanent・trash/ の中・/ext は完全に消す(trashed: false)
export const deleteSandboxPath = (path: string, options: { permanent?: boolean } = {}) =>
	postJson<{ ok: boolean; trashed: boolean; path?: string }>('/delete', {
		path,
		permanent: options.permanent ?? false
	});

export const renameSandboxPath = (path: string, dest: string) =>
	postJson<{ ok: boolean; path: string }>('/rename', { path, dest });

export const makeSandboxDir = (path: string) =>
	postJson<{ ok: boolean; path: string }>('/mkdir', { path });

// ダウンロードは cookie 認証の素の GET(<a href download> で開く)— 常に添付で返る
export const sandboxDownloadUrl = (path: string) =>
	`${BASE}/download?path=${encodeURIComponent(path)}`;
