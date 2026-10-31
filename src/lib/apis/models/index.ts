import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';

export const getModels = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/models/`, {
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

export const createNewModel = async (model: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/models/create`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(model)
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

export const getModelById = async (id: string) => {
	let error = null;

	const searchParams = new URLSearchParams();
	searchParams.append('id', id);

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/models/model?${searchParams.toString()}`, {
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

export const toggleModelById = async (id: string) => {
	let error = null;

	const searchParams = new URLSearchParams();
	searchParams.append('id', id);

	const res = await apiFetch(
		`${WEBUI_API_BASE_URL}/models/model/toggle?${searchParams.toString()}`,
		{
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			}
		}
	)
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

export const updateModelById = async (id: string, model: object) => {
	let error = null;

	const searchParams = new URLSearchParams();
	searchParams.append('id', id);

	const res = await apiFetch(
		`${WEBUI_API_BASE_URL}/models/model/update?${searchParams.toString()}`,
		{
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(model)
		}
	)
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

export const deleteModelById = async (id: string) => {
	let error = null;

	const searchParams = new URLSearchParams();
	searchParams.append('id', id);

	const res = await apiFetch(
		`${WEBUI_API_BASE_URL}/models/model/delete?${searchParams.toString()}`,
		{
			method: 'DELETE',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			}
		}
	)
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

export const deleteAllModels = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/models/delete/all`, {
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

// 編集中 (未保存) の preset で新しいチャットを始めたときにモデルへ渡る tools と system prompt を、
// 自分として展開して返す (POST /models/preview — admin のみ。実行はしない)
export type PreviewSegment = {
	text: string;
	// fixed = preset ごとに固定 / user = 利用者ごと / time = 日時で変わる / preset = preset の値 /
	// misplaced = その場所では展開しない変数 (空になる。text は '')
	scope: 'fixed' | 'user' | 'time' | 'preset' | 'misplaced';
	variable?: string;
};

// その場所では展開しない (空になる) 変数。where = system prompt / 会話の前置き / skill の本文
export type PreviewWarning = {
	where: 'system' | 'context' | 'skill';
	variable: string;
	message: string;
};

export type PreviewText = {
	text: string;
	segments: PreviewSegment[];
	// false = 断片に切れなかった (変数の値が前後とつながって別の変数になった等)。全体を 1 つで返す
	exact: boolean;
	chars: number;
};

export type PreviewTool = {
	name: string;
	tool_id?: string | null;
	description: string;
	parameters: Record<string, unknown>;
	chars: number;
	// 利用者ごとに変わる部分: 説明の description_from 文字目以降・params の enum・spec 全体 (overridden)
	varies?: { description_from?: number; params?: string[]; overridden?: boolean } | null;
};

export type ModelPreview = {
	tools: PreviewTool[];
	tools_chars: number;
	system: PreviewText;
	// 会話の前置き (params.context) の展開。framed = 最初の user 発言の先頭に入るバイト列そのもの (枠込み。空なら '')
	context: PreviewText & { framed: string };
	warnings: PreviewWarning[];
};

export const previewModel = async (
	model: object,
	variables: Record<string, string> = {}
): Promise<ModelPreview> => {
	const res = await apiFetch(`${WEBUI_API_BASE_URL}/models/preview`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ model, variables })
	});
	if (!res.ok) {
		const err = await res.json().catch(() => ({}));
		throw err?.detail ?? `${res.status}`;
	}
	return res.json();
};
