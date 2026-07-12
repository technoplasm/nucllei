import { apiFetch } from '$lib/apis/fetch';
import { OLLAMA_API_BASE_URL } from '$lib/constants';

export const verifyOllamaConnection = async (url: string = '', key: string = '') => {
	let error = null;

	const res = await apiFetch(`${OLLAMA_API_BASE_URL}/verify`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			url,
			key
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `Ollama: ${err?.error?.message ?? 'Network Problem'}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOllamaConfig = async () => {
	let error = null;

	const res = await apiFetch(`${OLLAMA_API_BASE_URL}/config`, {
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
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

type OllamaConfig = {
	ENABLE_OLLAMA_API: boolean;
	OLLAMA_BASE_URLS: string[];
	OLLAMA_API_CONFIGS: object;
};

export const updateOllamaConfig = async (config: OllamaConfig) => {
	let error = null;

	const res = await apiFetch(`${OLLAMA_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOllamaUrls = async () => {
	let error = null;

	const res = await apiFetch(`${OLLAMA_API_BASE_URL}/urls`, {
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
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res.OLLAMA_BASE_URLS;
};

export const getOllamaVersion = async (urlIdx?: number) => {
	let error = null;

	const res = await apiFetch(`${OLLAMA_API_BASE_URL}/api/version${urlIdx ? `/${urlIdx}` : ''}`, {
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
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.version ?? false;
};

export const getOllamaModels = async (urlIdx: null | number = null) => {
	let error = null;

	const res = await apiFetch(
		`${OLLAMA_API_BASE_URL}/api/tags${urlIdx !== null ? `/${urlIdx}` : ''}`,
		{
			method: 'GET',
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
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return (res?.models ?? [])
		.map((model) => ({ id: model.model, name: model.name ?? model.model, ...model }))
		.sort((a, b) => {
			return a.name.localeCompare(b.name);
		});
};

export const generateChatCompletion = async (body: object) => {
	let controller = new AbortController();
	let error = null;

	const res = await apiFetch(`${OLLAMA_API_BASE_URL}/api/chat`, {
		signal: controller.signal,
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
	}).catch((err) => {
		error = err;
		return null;
	});

	if (error) {
		throw error;
	}

	return [res, controller];
};

export const createModel = async (payload: object, urlIdx: string | null = null) => {
	let error = null;

	const res = await apiFetch(
		`${OLLAMA_API_BASE_URL}/api/create${urlIdx !== null ? `/${urlIdx}` : ''}`,
		{
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(payload)
		}
	).catch((err) => {
		error = err;
		return null;
	});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteModel = async (tagName: string, urlIdx: string | null = null) => {
	let error = null;

	const res = await apiFetch(
		`${OLLAMA_API_BASE_URL}/api/delete${urlIdx !== null ? `/${urlIdx}` : ''}`,
		{
			method: 'DELETE',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				name: tagName
			})
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			console.log(json);
			return true;
		})
		.catch((err) => {
			console.log(err);
			error = err;

			if ('detail' in err) {
				error = err.detail;
			}

			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const pullModel = async (tagName: string, urlIdx: number | null = null) => {
	let error = null;
	const controller = new AbortController();

	const res = await apiFetch(
		`${OLLAMA_API_BASE_URL}/api/pull${urlIdx !== null ? `/${urlIdx}` : ''}`,
		{
			signal: controller.signal,
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				name: tagName
			})
		}
	).catch((err) => {
		console.log(err);
		error = err;

		if ('detail' in err) {
			error = err.detail;
		}

		return null;
	});
	if (error) {
		throw error;
	}
	return [res, controller];
};

export const downloadModel = async (download_url: string, urlIdx: string | null = null) => {
	let error = null;

	const res = await apiFetch(
		`${OLLAMA_API_BASE_URL}/models/download${urlIdx !== null ? `/${urlIdx}` : ''}`,
		{
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				url: download_url
			})
		}
	).catch((err) => {
		console.log(err);
		error = err;

		if ('detail' in err) {
			error = err.detail;
		}

		return null;
	});
	if (error) {
		throw error;
	}
	return res;
};

export const uploadModel = async (file: File, urlIdx: string | null = null) => {
	let error = null;

	const formData = new FormData();
	formData.append('file', file);

	const res = await apiFetch(
		`${OLLAMA_API_BASE_URL}/models/upload${urlIdx !== null ? `/${urlIdx}` : ''}`,
		{
			method: 'POST',
			headers: {},
			body: formData
		}
	).catch((err) => {
		console.log(err);
		error = err;

		if ('detail' in err) {
			error = err.detail;
		}

		return null;
	});
	if (error) {
		throw error;
	}
	return res;
};

// export const pullModel = async (tagName: string) => {
// 	return await fetch(`${OLLAMA_API_BASE_URL}/pull`, {
// 		method: 'POST',
// 		headers: {
// 			'Content-Type': 'text/event-stream',
// 			Authorization: `Bearer ${token}`
// 		},
// 		body: JSON.stringify({
// 			name: tagName
// 		})
// 	});
// };
