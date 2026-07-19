import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';

// llens(#85): AccessControl のユーザー指定用ディレクトリ検索(verified 向け・最小フィールド)。
// query = 名前/メール部分一致、ids = カンマ区切り id 群の名前解決(既存 ACL のチップ表示用)
export const searchUsers = async (params: { query?: string; ids?: string[] }) => {
	const searchParams = new URLSearchParams();
	if (params.query) {
		searchParams.append('query', params.query);
	}
	if (params.ids?.length) {
		searchParams.append('ids', params.ids.join(','));
	}

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/search?${searchParams.toString()}`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	});
	if (!res.ok) throw await res.json();
	return (await res.json()) as { id: string; name: string; email: string }[];
};

export const getUserDefaultPermissions = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/default/permissions`, {
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

export const updateUserDefaultPermissions = async (permissions: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/default/permissions`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			...permissions
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

export const updateUserRole = async (id: string, role: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/update/role`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			id: id,
			role: role
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

export const getUsers = async () => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/`, {
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

	return res ? res : [];
};

export const getUserSettings = async () => {
	let error = null;
	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/user/settings`, {
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

export const updateUserSettings = async (settings: object) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/user/settings/update`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			...settings
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

export const getUserById = async (userId: string) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/${userId}`, {
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

// llens(#55): 本人による info 読み書き(getUserInfo/updateUserInfo)と geolocation
// (getAndUpdateUserLocation)は撤去。info は admin 専有(updateUserById の info で編集)。
export const getUserMetaFields = async () => {
	let error = null;
	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/meta/fields`, {
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

// llens(#69→#31): deleteUserById は撤去済み。user の hard delete は BE エンドポイントごと
// 撤去された(チャット証憑の保全)。無効化は role=disabled で行う(全 API 遮断 + 案内画面)。

type UserUpdateForm = {
	profile_image_url: string;
	email: string;
	name: string;
	password: string;
	// llens(#55): 追加 meta(宣言キーのみサーバーが受理)
	info?: object;
};

export const updateUserById = async (userId: string, user: UserUpdateForm) => {
	let error = null;

	const res = await apiFetch(`${WEBUI_API_BASE_URL}/users/${userId}/update`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			profile_image_url: user.profile_image_url,
			email: user.email,
			name: user.name,
			password: user.password !== '' ? user.password : undefined,
			// llens(#55): 追加 meta(宣言キーのみサーバーが受理)
			info: user.info
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
