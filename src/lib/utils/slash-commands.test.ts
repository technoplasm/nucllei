// `/` の補完(スキルとレシピ)の規則のテスト。サーバ側(nucllei/test/apps/webui/test_recipe_commands.py)と
// 同じ場面を固定する — 片方だけずれると、灰色でない行を選んでも別の物が呼ばれる。

import { describe, expect, it } from 'vitest';

import type { RecipeItem, RecipePlace } from '$lib/apis/recipes';
import {
	buildSlashMenu,
	commandSkillOf,
	commandToken,
	matchRank,
	recipeEntries,
	replaceCommandToken,
	resolveRecipeCommand,
	selectableEntries
} from './slash-commands';

const recipe = (
	name: string,
	place: RecipePlace,
	group = '',
	extra: Partial<RecipeItem> = {}
): RecipeItem => {
	const base =
		place === 'personal'
			? '/workspace'
			: place === 'group'
				? `/ext/groups/${group}`
				: '/ext/public';
	return {
		name,
		folder: `${base}/recipes/${name}`,
		place,
		group,
		title: `${name} の題`,
		description: `${name} の説明`,
		broken: [],
		warnings: [],
		...extra
	};
};

const skill = (id: string) => ({ id, name: `${id} の名前`, description: `${id} の説明` });

const blockedOf = (recipes: RecipeItem[], skills = [] as ReturnType<typeof skill>[]) =>
	recipeEntries(recipes, skills).map((e) => [e.place, e.group, e.name, e.blocked]);

describe('commandToken', () => {
	it('先頭行の行頭の / から空白まで', () => {
		expect(commandToken('/集計 今月分')).toBe('集計');
		expect(commandToken('/集計\n2 行目')).toBe('集計');
		expect(commandToken('/集計　全角の空白')).toBe('集計');
		expect(commandToken('/foo.bar baz')).toBe('foo.bar');
		expect(commandToken('/' + 'ガ')).toBe('ガ'); // NFC
	});

	it('行頭以外・先頭行以外・名前の無い / は null', () => {
		expect(commandToken('今月 /集計')).toBeNull();
		expect(commandToken('1 行目\n/集計')).toBeNull();
		expect(commandToken('/')).toBeNull();
		expect(commandToken('/ 集計')).toBeNull();
		expect(commandToken('')).toBeNull();
	});
});

describe('commandSkillOf: /name で発火する skill', () => {
	it('同じ id の skill', () => {
		expect(commandSkillOf('summary', [skill('summary')])).toBe('summary');
		expect(commandSkillOf('summary', [skill('other')])).toBeNull();
	});

	it('skill の /command は英数字・_・- の並び — foo.bar は skill foo に当たる', () => {
		expect(commandSkillOf('foo.bar', [skill('foo')])).toBe('foo');
		expect(commandSkillOf('集計', [skill('foo')])).toBeNull();
	});
});

describe('レシピ同士: 狭い置き場が勝つ', () => {
	it('個人 → グループ → 全体', () => {
		expect(
			blockedOf([
				recipe('集計', 'public'),
				recipe('集計', 'group', 'EMR'),
				recipe('集計', 'personal')
			])
		).toEqual([
			['personal', '', '集計', null],
			['group', 'EMR', '集計', { kind: 'recipe', place: 'personal', group: '', broken: false }],
			['public', '', '集計', { kind: 'recipe', place: 'personal', group: '', broken: false }]
		]);
	});

	it('グループ同士は一覧の順(サーバがグループ名の順に並べて返す)のまま', () => {
		expect(
			blockedOf([recipe('x', 'group', 'A'), recipe('x', 'group', 'b'), recipe('x', 'group', 'B2')])
		).toEqual([
			['group', 'A', 'x', null],
			['group', 'b', 'x', { kind: 'recipe', place: 'group', group: 'A', broken: false }],
			['group', 'B2', 'x', { kind: 'recipe', place: 'group', group: 'A', broken: false }]
		]);
	});

	it('置き場は一覧の順に依らず狭い順(個人 → グループ → 全体)', () => {
		expect(
			blockedOf([recipe('x', 'public'), recipe('x', 'group', 'b'), recipe('x', 'group', 'A')])
		).toEqual([
			['group', 'b', 'x', null],
			['group', 'A', 'x', { kind: 'recipe', place: 'group', group: 'b', broken: false }],
			['public', '', 'x', { kind: 'recipe', place: 'group', group: 'b', broken: false }]
		]);
	});

	it('壊れた狭い置き場のレシピが名前を持つ(広い置き場に回さない)', () => {
		expect(
			blockedOf([
				recipe('集計', 'personal', '', { broken: ['RECIPE.md が無い'] }),
				recipe('集計', 'public')
			])
		).toEqual([
			['personal', '', '集計', { kind: 'broken', reasons: ['RECIPE.md が無い'] }],
			['public', '', '集計', { kind: 'recipe', place: 'personal', group: '', broken: true }]
		]);
	});

	it('警告は選べる(警告の理由は行に残す)', () => {
		const [entry] = recipeEntries(
			[recipe('集計', 'personal', '', { warnings: ['requires の 1 番目の read が無い'] })],
			[]
		);
		expect(entry.blocked).toBeNull();
		expect(entry.warnings).toEqual(['requires の 1 番目の read が無い']);
	});

	it('名前に空白があるレシピは / では呼べない', () => {
		expect(blockedOf([recipe('月次 集計', 'personal')])).toEqual([
			['personal', '', '月次 集計', { kind: 'space' }]
		]);
	});
});

describe('skill とレシピ: skill が先', () => {
	it('同じ名前のレシピはどの置き場でも灰色', () => {
		expect(
			blockedOf(
				[
					recipe('summary', 'personal'),
					recipe('summary', 'group', 'EMR'),
					recipe('summary', 'public')
				],
				[skill('summary')]
			)
		).toEqual([
			['personal', '', 'summary', { kind: 'skill', skill: 'summary' }],
			['group', 'EMR', 'summary', { kind: 'skill', skill: 'summary' }],
			['public', '', 'summary', { kind: 'skill', skill: 'summary' }]
		]);
	});

	it('壊れていても skill の理由が先', () => {
		expect(
			blockedOf([recipe('summary', 'personal', '', { broken: ['x'] })], [skill('summary')])
		).toEqual([['personal', '', 'summary', { kind: 'skill', skill: 'summary' }]]);
	});

	it('/foo.bar のレシピは skill foo に取られる', () => {
		expect(blockedOf([recipe('foo.bar', 'personal')], [skill('foo')])).toEqual([
			['personal', '', 'foo.bar', { kind: 'skill', skill: 'foo' }]
		]);
	});

	it('このモデルで発火しない skill(一覧に無い)とは衝突しない', () => {
		expect(blockedOf([recipe('summary', 'personal')], [])).toEqual([
			['personal', '', 'summary', null]
		]);
	});
});

describe('resolveRecipeCommand: 送信した /name で呼ばれるもの(サーバと同じ)', () => {
	const recipes = [
		recipe('集計', 'public'),
		recipe('集計', 'group', 'EMR'),
		recipe('集計', 'personal'),
		recipe('抽出', 'group', 'EMR'),
		recipe('抽出', 'group', '病理'),
		recipe('壊れ', 'personal', '', { broken: ['x'] }),
		recipe('壊れ', 'public'),
		recipe('summary', 'personal')
	];

	it('狭い置き場の物', () => {
		expect(resolveRecipeCommand('/集計 今月', recipes, [])?.folder).toBe('/workspace/recipes/集計');
		expect(resolveRecipeCommand('/抽出', recipes, [])?.folder).toBe('/ext/groups/EMR/recipes/抽出');
	});

	it('勝った物が壊れていれば null・skill が先・無い名前は null', () => {
		expect(resolveRecipeCommand('/壊れ', recipes, [])).toBeNull();
		expect(resolveRecipeCommand('/summary', recipes, [skill('summary')])).toBeNull();
		expect(resolveRecipeCommand('/summary', recipes, [])?.place).toBe('personal');
		expect(resolveRecipeCommand('/無い', recipes, [])).toBeNull();
		expect(resolveRecipeCommand('/集', recipes, [])).toBeNull();
	});

	it('灰色でない行と、送ったときに呼ばれる物が一致する', () => {
		for (const entry of recipeEntries(recipes, [skill('summary')])) {
			const resolved = resolveRecipeCommand(`/${entry.name}`, recipes, [skill('summary')]);
			if (entry.blocked === null) {
				expect(resolved?.folder).toBe(`${entry.key.slice('recipe:'.length)}`);
			} else {
				expect(resolved?.folder).not.toBe(entry.key.slice('recipe:'.length));
			}
		}
	});
});

describe('matchRank', () => {
	it('前方一致 0・部分一致 1・合わない -1(大文字小文字を区別しない)', () => {
		expect(matchRank('Summary', 'sum')).toBe(0);
		expect(matchRank('monthly-summary', 'SUM')).toBe(1);
		expect(matchRank('集計', 'x')).toBe(-1);
		expect(matchRank('集計', '')).toBe(0);
	});
});

describe('buildSlashMenu', () => {
	const recipes = [
		recipe('集計', 'personal'),
		recipe('集計', 'group', 'EMR'),
		recipe('抽出', 'group', '病理'),
		recipe('共通', 'public'),
		recipe('summary', 'public')
	];
	const skills = [skill('summary'), skill('kansai')];

	it('個人 → グループ → 全体 → スキルの順、空の節は出さない', () => {
		const sections = buildSlashMenu({ skills, recipes, query: '' });
		expect(sections.map((s) => [s.id, s.entries.map((e) => e.name)])).toEqual([
			['personal', ['集計']],
			['group', ['集計', '抽出']],
			['public', ['共通', 'summary']],
			['skill', ['kansai', 'summary']]
		]);
		expect(buildSlashMenu({ skills: [], recipes: [recipe('共通', 'public')], query: '' })).toEqual([
			expect.objectContaining({ id: 'public' })
		]);
	});

	it('recipes が null ならスキルだけ', () => {
		expect(buildSlashMenu({ skills, recipes: null, query: '' }).map((s) => s.id)).toEqual([
			'skill'
		]);
	});

	it('各節を前方一致 → 部分一致の順に絞る', () => {
		const rs = [
			recipe('月次集計', 'personal'),
			recipe('集計表', 'personal'),
			recipe('抽出', 'personal')
		];
		const sections = buildSlashMenu({ skills: [], recipes: rs, query: '集計' });
		expect(sections[0].entries.map((e) => e.name)).toEqual(['集計表', '月次集計']);
		const sk = buildSlashMenu({
			skills: [skill('my-sum'), skill('summary')],
			recipes: null,
			query: 'sum'
		});
		expect(sk[0].entries.map((e) => e.name)).toEqual(['summary', 'my-sum']);
	});

	it('選べる行は灰色を飛ばす', () => {
		const sections = buildSlashMenu({ skills, recipes, query: '' });
		expect(selectableEntries(sections).map((e) => e.key)).toEqual([
			'recipe:/workspace/recipes/集計',
			'recipe:/ext/groups/病理/recipes/抽出',
			'recipe:/ext/public/recipes/共通',
			'skill:kansai',
			'skill:summary'
		]);
	});
});

describe('replaceCommandToken', () => {
	it('先頭の / の語を /name に置き換える', () => {
		expect(replaceCommandToken('/集', '集計')).toBe('/集計 ');
		expect(replaceCommandToken('/', 'summary')).toBe('/summary ');
		expect(replaceCommandToken('/集\n2 行目', '集計')).toBe('/集計 \n2 行目');
	});
});
