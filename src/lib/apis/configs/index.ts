import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { Banner } from '$lib/types';

export const importConfig = async (config) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/import`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			config: config
		})
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

export const exportConfig = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/export`, {
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

export const getToolServerConnections = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/tool_servers`, {
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

export const setToolServerConnections = async (connections: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/tool_servers`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			...connections
		})
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

export const verifyToolServerConnection = async (connection: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/tool_servers/verify`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			...connection
		})
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

// llens(#67): 添付ゲート設定(retrieval router 撤去に伴い /configs/attachment へ移設)
export const getAttachmentConfig = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/attachment`, {
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

export const setAttachmentConfig = async (config: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/attachment`, {
		method: 'POST',
		headers: {
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
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getCodeInterpreterConfig = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/code_interpreter`, {
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

export const setCodeInterpreterConfig = async (config: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/code_interpreter`, {
		method: 'POST',
		headers: {
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
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getModelsConfig = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/models`, {
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

export const setModelsConfig = async (config: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/models`, {
		method: 'POST',
		headers: {
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
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getBanners = async (): Promise<Banner[]> => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/banners`, {
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

export const setBanners = async (banners: Banner[]) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/configs/banners`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			banners: banners
		})
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
