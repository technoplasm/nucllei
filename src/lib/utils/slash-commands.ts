// 入力欄の `/` の補完(スキルとレシピ)。どれが使われるかの規則はサーバ(nucllei/utils/recipe_commands.py と
// utils/skills.py の command_skill)と同じに保つこと(片方だけ変えない):
// - skill が先: name 全体が発火する skill の id と一致すれば、同じ名前のレシピはどの置き場でも使われない
//   (前方一致では当てない — `/emr抽出` はレシピ「emr抽出」、`/emr` と `/emr 抽出して` は skill emr)
// - レシピ同士は狭い置き場が勝つ: 個人 → グループ(グループ名の順)→ 全体。グループの順はサーバが返す一覧の順
//   (GET /api/v1/recipes/ — サーバの precedence と同じ鍵で並べてある)をそのまま使う
// - 勝ったレシピが壊れていれば何も呼ばない(広い置き場の同じ名前に回さない)
// - 名前は空白までの 1 語(空白のあるレシピは `/` では呼べない)
// 使われない行も一覧には出し、灰色にして選べなくする(理由を添える)。

import type { RecipeItem, RecipePlace } from '$lib/apis/recipes';
import { matchSkillCommand } from './skills';

const PLACE_ORDER: Record<RecipePlace, number> = { personal: 0, group: 1, public: 2 };

export type SlashSkill = { id: string; name: string; description?: string | null };

// 灰色の理由
export type SlashBlocked =
	| { kind: 'skill'; skill: string } // スキル「…」が使われる
	| { kind: 'recipe'; place: RecipePlace; group: string; broken: boolean } // 狭い置き場のレシピが先(それが壊れていれば何も呼ばれない)
	| { kind: 'broken'; reasons: string[] } // 壊れている
	| { kind: 'space' }; // 名前に空白がある

export type SlashEntry = {
	key: string;
	kind: 'skill' | 'recipe';
	name: string; // `/` の後に入る名前(skill の id・レシピのフォルダ名)
	title: string; // skill の名前・レシピの title
	description: string;
	place: RecipePlace | null;
	group: string;
	warnings: string[];
	blocked: SlashBlocked | null;
};

export type SlashSectionId = RecipePlace | 'skill';

export type SlashSection = { id: SlashSectionId; entries: SlashEntry[] };

// 先頭行の行頭の `/name` の name(`/` から空白・行末まで。NFC)。サーバの command_name と同じ。
export const commandToken = (text: string | null | undefined): string | null =>
	text ? matchSkillCommand(text) : null;

// `/name` で発火する skill の id(無ければ null)。name 全体が skill の id と一致するときだけ。
// skills は選んでいるモデルで発火しうる skill。
export const commandSkillOf = (name: string, skills: SlashSkill[]): string | null =>
	skills.some((s) => s.id === name) ? name : null;

// 置き場の狭い順。同じ置き場の中(グループ同士)は一覧の順のまま(サーバがグループ名の順に並べて返す —
// 文字の比べ方を FE で書き直さない)
const byPlace = (recipes: RecipeItem[]) =>
	recipes
		.map((recipe, index) => ({ recipe, index }))
		.sort(
			(a, b) =>
				(PLACE_ORDER[a.recipe.place] ?? 3) - (PLACE_ORDER[b.recipe.place] ?? 3) || a.index - b.index
		)
		.map(({ recipe }) => recipe);

// レシピの行(狭い置き場の順)と、それぞれが使われない理由。
export const recipeEntries = (recipes: RecipeItem[], skills: SlashSkill[]): SlashEntry[] => {
	const winners = new Map<string, RecipeItem>();
	return byPlace(recipes).map((recipe) => {
		let blocked: SlashBlocked | null = null;
		if (/\s/.test(recipe.name)) {
			blocked = { kind: 'space' };
		} else {
			const skill = commandSkillOf(recipe.name, skills);
			const winner = winners.get(recipe.name);
			if (skill) {
				blocked = { kind: 'skill', skill };
			} else if (winner) {
				blocked = {
					kind: 'recipe',
					place: winner.place,
					group: winner.group,
					broken: winner.broken.length > 0
				};
			} else if (recipe.broken.length > 0) {
				blocked = { kind: 'broken', reasons: recipe.broken };
			}
			if (!winner) {
				winners.set(recipe.name, recipe);
			}
		}
		return {
			key: `recipe:${recipe.folder}`,
			kind: 'recipe',
			name: recipe.name,
			title: recipe.title,
			description: recipe.description,
			place: recipe.place,
			group: recipe.group,
			warnings: recipe.warnings,
			blocked
		};
	});
};

// `/name` で呼ばれるレシピ(サーバの resolve_recipe_command + skill が先)。無ければ null。
export const resolveRecipeCommand = (
	text: string,
	recipes: RecipeItem[],
	skills: SlashSkill[]
): RecipeItem | null => {
	const name = commandToken(text);
	if (name === null || commandSkillOf(name, skills)) {
		return null;
	}
	const winner = byPlace(recipes.filter((r) => r.name === name))[0];
	return winner && winner.broken.length === 0 ? winner : null;
};

// 0 = 前方一致、1 = 部分一致、-1 = 合わない(大文字小文字は区別しない。空の query は全部前方一致)
export const matchRank = (name: string, query: string): number => {
	const n = name.normalize('NFC').toLowerCase();
	const q = query.normalize('NFC').toLowerCase();
	if (n.startsWith(q)) {
		return 0;
	}
	return n.includes(q) ? 1 : -1;
};

const filterByQuery = (entries: SlashEntry[], query: string) =>
	entries
		.map((entry, index) => ({ entry, index, rank: matchRank(entry.name, query) }))
		.filter((x) => x.rank >= 0)
		.sort((a, b) => a.rank - b.rank || a.index - b.index)
		.map((x) => x.entry);

const SECTION_ORDER: SlashSectionId[] = ['personal', 'group', 'public', 'skill'];

// 補完の一覧(個人のレシピ → グループのレシピ → 全体のレシピ → スキル)。各節は query の前方一致 → 部分一致の順、
// その中は元の順(レシピは一覧の順、スキルは名前の順)。空の節は出さない。
// recipes が null(能力「レシピ」OFF・sandbox 無し・取れない)ならレシピの節は無い。
export const buildSlashMenu = ({
	skills,
	recipes,
	query
}: {
	skills: SlashSkill[];
	recipes: RecipeItem[] | null;
	query: string;
}): SlashSection[] => {
	const recipeRows = recipes ? recipeEntries(recipes, skills) : [];
	const skillRows: SlashEntry[] = [...skills]
		.sort((a, b) => a.name.localeCompare(b.name))
		.map((s) => ({
			key: `skill:${s.id}`,
			kind: 'skill',
			name: s.id,
			title: s.name,
			description: s.description ?? '',
			place: null,
			group: '',
			warnings: [],
			blocked: null
		}));
	return SECTION_ORDER.map((id) => ({
		id,
		entries: filterByQuery(
			id === 'skill' ? skillRows : recipeRows.filter((r) => r.place === id),
			query
		)
	})).filter((section) => section.entries.length > 0);
};

// 選べる行(キーボードの上下で移るのはこれだけ — 灰色の行は飛ばす)
export const selectableEntries = (sections: SlashSection[]): SlashEntry[] =>
	sections.flatMap((section) => section.entries).filter((entry) => entry.blocked === null);

// 入力の先頭の `/…`(入力途中の部分)を `/name ` に置き換える(先頭行だけ)
export const replaceCommandToken = (prompt: string, name: string): string => {
	const lines = prompt.split('\n');
	const first = (lines.shift() ?? '').replace(/^\/\S*/, `/${name} `);
	return [first, ...lines].join('\n');
};
