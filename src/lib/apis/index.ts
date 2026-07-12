import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
import { convertOpenApiToToolPayload } from '$lib/utils';

import { parse } from 'yaml';
import { toast } from 'svelte-sonner';

export const getModels = async (base: boolean = false) => {
	let error = null;
	const res = await apiFetch(`${WEBUI_BASE_URL}/api/models${base ? '/base' : ''}`, {
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

	return res?.data ?? [];
};

type ChatCompletedForm = {
	model: string;
	messages: string[];
	chat_id: string;
	session_id: string;
};

export const chatCompleted = async (body: ChatCompletedForm) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/chat/completed`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

type ChatActionForm = {
	model: string;
	messages: string[];
	chat_id: string;
};

export const chatAction = async (action_id: string, body: ChatActionForm) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/chat/actions/${action_id}`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const stopTask = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/tasks/stop/${id}`, {
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
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getTaskIdsByChatId = async (chat_id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/tasks/chat/${chat_id}`, {
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// 注: この token は llens セッションではなく「外部 tool server の資格情報」(bearer key)。
// cookie 認証化(#70 B1)の対象外(session 型 = セッション JWT 転送は cookie 化で不成立のため廃止)。
export const getToolServerData = async (token: string, url: string) => {
	let error = null;

	const res = await fetch(`${url}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			// Check if URL ends with .yaml or .yml to determine format
			if (url.toLowerCase().endsWith('.yaml') || url.toLowerCase().endsWith('.yml')) {
				if (!res.ok) throw await res.text();
				const text = await res.text();
				return parse(text);
			} else {
				if (!res.ok) throw await res.json();
				return res.json();
			}
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	const data = {
		openapi: res,
		info: res.info,
		specs: convertOpenApiToToolPayload(res)
	};

	console.log(data);
	return data;
};

export const getTaskConfig = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/tasks/config`, {
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
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateTaskConfig = async (config: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/tasks/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(config)
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const generateTitle = async (model: string, messages: string[], chat_id?: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/tasks/title/completions`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			model: model,
			messages: messages,
			...(chat_id && { chat_id: chat_id })
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
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.choices[0]?.message?.content.replace(/["']/g, '') ?? 'New Chat';
};

export const getPipelinesList = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/pipelines/list`, {
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
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	let pipelines = res?.data ?? [];
	return pipelines;
};

export const uploadPipeline = async (file: File, urlIdx: string) => {
	let error = null;

	// Create a new FormData object to handle the file upload
	const formData = new FormData();
	formData.append('file', file);
	formData.append('urlIdx', urlIdx);

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/pipelines/upload`, {
		method: 'POST',
		headers: {
			// 'Content-Type': 'multipart/form-data' is not needed as Fetch API will set it automatically
		},
		body: formData
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const downloadPipeline = async (url: string, urlIdx: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/pipelines/add`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			url: url,
			urlIdx: urlIdx
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deletePipeline = async (id: string, urlIdx: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/pipelines/delete`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			id: id,
			urlIdx: urlIdx
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getPipelines = async (urlIdx?: string) => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (urlIdx !== undefined) {
		searchParams.append('urlIdx', urlIdx);
	}

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/v1/pipelines/?${searchParams.toString()}`, {
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
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	let pipelines = res?.data ?? [];
	return pipelines;
};

export const getPipelineValves = async (pipeline_id: string, urlIdx: string) => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (urlIdx !== undefined) {
		searchParams.append('urlIdx', urlIdx);
	}

	const res = await apiFetch(
		`${WEBUI_BASE_URL}/api/v1/pipelines/${pipeline_id}/valves?${searchParams.toString()}`,
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
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getPipelineValvesSpec = async (pipeline_id: string, urlIdx: string) => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (urlIdx !== undefined) {
		searchParams.append('urlIdx', urlIdx);
	}

	const res = await apiFetch(
		`${WEBUI_BASE_URL}/api/v1/pipelines/${pipeline_id}/valves/spec?${searchParams.toString()}`,
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
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updatePipelineValves = async (pipeline_id: string, valves: object, urlIdx: string) => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (urlIdx !== undefined) {
		searchParams.append('urlIdx', urlIdx);
	}

	const res = await apiFetch(
		`${WEBUI_BASE_URL}/api/v1/pipelines/${pipeline_id}/valves/update?${searchParams.toString()}`,
		{
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(valves)
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
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getBackendConfig = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/config`, {
		method: 'GET',
		credentials: 'include',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getChangelog = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/changelog`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getVersionUpdates = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/version/updates`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getModelFilterConfig = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/config/model/filter`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateModelFilterConfig = async (enabled: boolean, models: string[]) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/config/model/filter`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			enabled: enabled,
			models: models
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getWebhookUrl = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/webhook`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res.url;
};

export const updateWebhookUrl = async (url: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/webhook`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			url: url
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res.url;
};

export const getCommunitySharingEnabledStatus = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/community_sharing`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const toggleCommunitySharingEnabledStatus = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/community_sharing/toggle`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getModelConfig = async (): Promise<GlobalModelConfig> => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/config/models`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res.models;
};

export interface ModelConfig {
	id: string;
	name: string;
	meta: ModelMeta;
	base_model_id?: string;
	params: ModelParams;
}

export interface ModelMeta {
	description?: string;
	capabilities?: object;
	profile_image_url?: string;
}

export interface ModelParams {}

export type GlobalModelConfig = ModelConfig[];

export const updateModelConfig = async (config: GlobalModelConfig) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_BASE_URL}/api/config/models`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			models: config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
