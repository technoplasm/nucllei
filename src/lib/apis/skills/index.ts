import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';

// Skill は Prompt の後継(issue #35)。llens(#43): 可読キーは id 一本(旧 command)。
// id が /command 発火キー兼 PK でバックエンドが先頭 `/` なしに正規化する。表示名は name(旧 title)。
export type SkillItem = {
	id: string;
	name: string;
	description?: string | null;
	content: string;
	tool_ids?: string[];
	access_control?: null | object;
};

export const createNewSkill = async (skill: SkillItem) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/skills/create`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(skill)
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

export const getSkills = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/skills/`, {
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

export const getSkillList = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/skills/list`, {
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

export const getSkillById = async (id: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/skills/id/${id}`, {
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

export const updateSkillById = async (skill: SkillItem) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/skills/id/${skill.id}/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(skill)
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

export const deleteSkillById = async (id: string) => {
	let error = null;

	id = id.charAt(0) === '/' ? id.slice(1) : id;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/skills/id/${id}/delete`, {
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
