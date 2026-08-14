import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { ChatListItem } from '$lib/stores';
import { getTimeRange } from '$lib/utils';

export const createNewChat = async (chat: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/new`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			chat: chat
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const importChat = async (chat: object, meta: object | null, pinned?: boolean) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/import`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			chat: chat,
			meta: meta ?? {},
			pinned: pinned
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getChatList = async (page: number | null = null) => {
	let error = null;
	const searchParams = new URLSearchParams();

	if (page !== null) {
		searchParams.append('page', `${page}`);
	}

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/?${searchParams.toString()}`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res.map((chat: ChatListItem) => ({
		...chat,
		time_range: getTimeRange(chat.updated_at)
	}));
};

export const getChatListByUserId = async (userId: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/list/user/${userId}`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res.map((chat: ChatListItem) => ({
		...chat,
		time_range: getTimeRange(chat.updated_at)
	}));
};

// llens(#19): admin チャット閲覧ビュー。一覧はサーバーページング(GET /chats/admin/list)。
type AdminChatListOptions = {
	limit?: number;
	offset?: number;
	order_by?: 'created_at' | 'updated_at';
	direction?: 'desc' | 'asc';
	user_id?: string;
	title?: string;
	model?: string;
	updated_from?: number;
	updated_to?: number;
	// llens(#31): soft delete 済みチャットの扱い(未指定 = include)
	deleted?: 'include' | 'exclude' | 'only';
	// llens(#19): 共有状態(share_id の有無)の扱い(未指定 = include)
	shared?: 'include' | 'exclude' | 'only';
};

export const getAdminChatList = async (options: AdminChatListOptions = {}) => {
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
	if (options.user_id) {
		searchParams.append('user_id', options.user_id);
	}
	if (options.title) {
		searchParams.append('title', options.title);
	}
	if (options.model) {
		searchParams.append('model', options.model);
	}
	if (options.updated_from != null) {
		searchParams.append('updated_from', `${options.updated_from}`);
	}
	if (options.updated_to != null) {
		searchParams.append('updated_to', `${options.updated_to}`);
	}
	if (options.deleted) {
		searchParams.append('deleted', options.deleted);
	}
	if (options.shared) {
		searchParams.append('shared', options.shared);
	}

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/admin/list?${searchParams.toString()}`, {
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
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getAdminChatUsers = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/admin/users`, {
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
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getAdminChatModels = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/admin/models`, {
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
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getArchivedChatList = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/archived`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getAllChats = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/all`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getChatListBySearchText = async (text: string, page: number = 1) => {
	let error = null;

	const searchParams = new URLSearchParams();
	searchParams.append('text', text);
	searchParams.append('page', `${page}`);

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/search?${searchParams.toString()}`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res.map((chat: ChatListItem) => ({
		...chat,
		time_range: getTimeRange(chat.updated_at)
	}));
};

export const getAllArchivedChats = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/all/archived`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getAllUserChats = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/all/db`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getPinnedChatList = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/pinned`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res.map((chat: ChatListItem) => ({
		...chat,
		time_range: getTimeRange(chat.updated_at)
	}));
};

export const getChatById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}`, {
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

// llens(#107): 親チャット直下の agent session 一覧(GET /chats/{id}/children — P1 実装済み)。
// サイドバー tree と親画面の子セッション表示が読む。
export type ChatChildItem = {
	id: string;
	title: string;
	kind: string;
	updated_at: number;
	created_at: number;
	parent_message_id: string | null;
};

export const getChatChildren = async (id: string): Promise<ChatChildItem[]> => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/children`, {
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

// llens(#110): chat の execution 行一覧(GET /chats/{id}/executions)。
// バックグラウンドチップ・ExecutionBlock・poll 述語のデータ源。
// result 本文は載せない(回収は await_task — 一覧は状態のみ)。
export type ChatExecutionItem = {
	id: string;
	tool_name: string;
	status: string; // running|done|failed|stopped|orphaned
	created_at: number; // epoch 秒
	finished_at: number | null;
	duration: number | null;
	error: string | null;
	// 配達済み(terminal の結果が通知ターン or 回収 tool で消化された)。
	// 未配達(terminal ∧ !delivered)がチップの未回収バッジ・poll 継続の根拠
	delivered: boolean;
};

export type ChatExecutionsResponse = {
	executions: ChatExecutionItem[];
	// 連鎖上限で BE の自動再開(配達)が保留中(チップの案内行)。
	// BE 未実装の間は undefined — 消費側は falsy として扱う
	resume_suspended?: boolean;
};

export const getChatExecutions = async (id: string): Promise<ChatExecutionsResponse> => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/executions`, {
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

	// top-level resume_suspended 付きのオブジェクト形が正。素の配列は BE 追随までの
	// 過渡形として受ける(resume_suspended は undefined のまま)
	return Array.isArray(res) ? { executions: res } : res;
};

// llens(#110): execution の個別停止(POST /chats/{id}/executions/{exec_id}/stop)。
// stopped へ CAS → task cancel は BE 側。冪等(既に終端なら no-op 相当)。
export const stopChatExecution = async (id: string, execId: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/executions/${execId}/stop`, {
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

export const getChatByShareId = async (share_id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/share/${share_id}`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getChatPinnedStatusById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/pinned`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;

			if ('detail' in err) {
				error = err.detail;
			} else {
				error = err;
			}

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const toggleChatPinnedStatusById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/pin`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;

			if ('detail' in err) {
				error = err.detail;
			} else {
				error = err;
			}

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const cloneChatById = async (id: string, title?: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/clone`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			...(title && { title: title })
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
			error = err;

			if ('detail' in err) {
				error = err.detail;
			} else {
				error = err;
			}

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const cloneSharedChatById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/clone/shared`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;

			if ('detail' in err) {
				error = err.detail;
			} else {
				error = err;
			}

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const shareChatById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/share`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const archiveChatById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/archive`, {
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
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteSharedChatById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}/share`, {
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
			error = err;

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateChatById = async (id: string, chat: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			chat: chat
		})
	})
		.then(async (res) => {
			// llens(#110): status を同梱 — BE のサーバ駆動配達との競合(400)を
			// 呼び出し側(session.saveChatHandler)が接ぎ直しリカバリで判別する
			if (!res.ok) throw { status: res.status, ...(await res.json().catch(() => ({}))) };
			return res.json();
		})
		.then((json) => {
			return json;
		})
		.catch((err) => {
			error = err;

			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteChatById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/${id}`, {
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

export const deleteAllChats = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/`, {
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

export const archiveAllChats = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/chats/archive/all`, {
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
