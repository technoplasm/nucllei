import { RETRIEVAL_API_BASE_URL } from '$lib/constants';

export const getRAGConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${RETRIEVAL_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
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

type RAGConfigForm = {
	ENABLE_GOOGLE_DRIVE_INTEGRATION?: boolean;
	ENABLE_ONEDRIVE_INTEGRATION?: boolean;
	RAG_TEMPLATE?: string;
	ATTACHMENT_MAX_SIZE?: number | null;
	ATTACHMENT_MAX_COUNT?: number | null;
	ATTACHMENT_ALLOWED_EXTENSIONS?: string[];
	web?: Record<string, unknown>;
};

export const updateRAGConfig = async (token: string, payload: RAGConfigForm) => {
	let error = null;

	const res = await fetch(`${RETRIEVAL_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...payload
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

export interface SearchDocument {
	status: boolean;
	collection_name: string;
	filenames: string[];
}

export const processWebSearch = async (
	token: string,
	query: string,
	collection_name?: string
): Promise<SearchDocument | null> => {
	let error = null;

	const res = await fetch(`${RETRIEVAL_API_BASE_URL}/process/web/search`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			query,
			collection_name: collection_name ?? ''
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
