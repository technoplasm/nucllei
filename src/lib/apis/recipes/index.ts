import { apiFetch } from '$lib/apis/fetch';
import { WEBUI_API_BASE_URL } from '$lib/constants';

// 見えるレシピの一覧(nucllei/routers/recipes.py — 入力欄の `/` の補完が読む)。
// 一覧は load_recipe の説明と同じ物: 個人 → 所属グループ(名前順)→ 全体。管理者でも所属分だけ。
export type RecipePlace = 'personal' | 'group' | 'public';

export type RecipeItem = {
	name: string; // フォルダ名 = `/name` の name
	folder: string;
	place: RecipePlace;
	group: string; // place が group のときのグループ名
	title: string;
	description: string;
	broken: string[]; // 壊れている理由(空なら壊れていない)
	warnings: string[]; // 警告の理由(読み込めるが、通らない要素は実行しない)
};

export type RecipeList = {
	available: boolean; // sandbox のある配備か
	error: 'old' | 'unreachable' | null; // 一覧を取れなかった
	recipes: RecipeItem[];
	// この利用者が `/` で発火できる skill の id(持ち主か read 可視 — 管理者でも ACL で見る)
	skill_ids: string[];
};

export const getRecipes = async (): Promise<RecipeList> => {
	const res = await apiFetch(`${WEBUI_API_BASE_URL}/recipes/`, {
		method: 'GET',
		headers: { Accept: 'application/json' }
	});
	if (!res.ok) {
		const body = await res.json().catch(() => null);
		throw body?.detail ?? res.statusText;
	}
	return res.json();
};
