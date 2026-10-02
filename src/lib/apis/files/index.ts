import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';

// llens(#69): model_id クエリは撤去(per-model Attachment 廃止)。
// グローバル Attachment(ATTACHMENT_FUNCTION_ID)は upload 時にサーバー側で発火する。
// dictation は音声入力の通し録音に付ける印(meta.dictation)。backend が形を検証して受ける
// 唯一の meta で、それ以外のキーをクライアントから meta へ書き込む口は無い。
export const uploadFile = async (file: File, dictation?: Record<string, unknown>) => {
	const data = new FormData();
	data.append('file', file);
	if (dictation) {
		data.append('dictation', JSON.stringify(dictation));
	}
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/`, {
		method: 'POST',
		headers: {
			Accept: 'application/json'
		},
		body: data
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// llens(#69): ファイル管理ページ用のページング一覧。
// 返却は {"items": [FileModel(data は status のみ・content 無し)], "total": n}。
// user_ids は admin 専用フィルタ(非 admin が渡すと 403)。
// deleted(#94)は soft delete 済み行の扱い(admin 専用、既定 exclude — 非 admin が exclude 以外を渡すと 403)。
type FileListOptions = {
	limit?: number;
	offset?: number;
	order_by?: 'created_at' | 'accessed_at' | 'filename';
	direction?: 'desc' | 'asc';
	user_ids?: string[];
	filename?: string;
	deleted?: 'exclude' | 'only' | 'include';
};

export const getFileList = async (options: FileListOptions = {}) => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (options.limit != null) {
		searchParams.append('limit', `${options.limit}`);
	}
	if (options.offset != null) {
		searchParams.append('offset', `${options.offset}`);
	}
	if (options.order_by) {
		searchParams.append('order_by', options.order_by);
	}
	if (options.direction) {
		searchParams.append('direction', options.direction);
	}
	const userIds = options.user_ids ?? [];
	if (userIds.length > 0) {
		searchParams.append('user_ids', userIds.join(','));
	}
	if (options.filename) {
		searchParams.append('filename', options.filename);
	}
	if (options.deleted && options.deleted !== 'exclude') {
		searchParams.append('deleted', options.deleted);
	}

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/list?${searchParams.toString()}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// llens(#69): file を 1 件以上持つユーザーの一覧(admin 専用)。[{id, name, email, file_count}]
export const getFileUploaders = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/uploaders`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// llens(#69): この file を参照しているチャット。[{id, user_id, title, updated_at}]
// 非 admin は本人のチャットのみ。admin は全ユーザー(他人の分は /s/{id} で開く)。
export const getFileChats = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}/chats`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// llens(#69): 再添付 — Attachment フックを再発火する(owner/admin のみサーバー側で発火)。
export const processFileById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}/process`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// llens(#69): file の ACL 更新(owner/admin のみ)。file の ACL 意味論は他エンティティと逆:
// null=非公開(owner のみ)/ {read:{public:true}}=全体公開 / {read:{group_ids,user_ids}}=列挙先に公開。
export const updateFileAccessControlById = async (id: string, accessControl: null | object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}/access_control/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			access_control: accessControl
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// #94: 呼び出し側が 404(purge 済み共有 file 等)を他エラーと区別できるよう、
// HTTP エラーは status 付きの Error で投げる(表示は err.message を使う)。
// ネットワーク断は apiFetch 由来の例外がそのまま飛ぶ(status なし)。
export const getFileById = async (id: string) => {
	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	});

	if (!res.ok) {
		const detail = (await res.json().catch(() => null))?.detail;
		const err = Object.assign(new Error(typeof detail === 'string' ? detail : res.statusText), {
			status: res.status
		});
		console.log(err);
		throw err;
	}

	return res.json();
};

export const updateFileDataContentById = async (id: string, content: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}/data/content/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			content: content
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteFileById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// llens(#94): soft delete 済み file を復元(admin 専用。purged / active は 400)。
export const restoreFileById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}/restore`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// llens(#94): soft delete 済み file の実体を破棄(admin 専用。active / 既 purged は 400)。
// Storage の実体と抽出テキスト(data)を消すため復元不能になる。
export const purgeFileById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/files/${id}/purge`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
