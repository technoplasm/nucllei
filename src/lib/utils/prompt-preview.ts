// preset / skill の編集画面のプレビュー (POST /models/preview・/skills/preview の結果) の表示用の計算。

import type { PreviewTool } from '$lib/apis/models';

// トークン数の概算: ASCII は 4 文字 ≈ 1、それ以外 (日本語など) は 1 文字 ≈ 1。手元にモデルの
// トークナイザが無いので目安だけ (画面にこの式を注記する)
export const estimateTokens = (text: string): number => {
	let ascii = 0;
	let other = 0;
	for (const ch of text) {
		if ((ch.codePointAt(0) ?? 0) < 0x80) ascii += 1;
		else other += 1;
	}
	return Math.ceil(ascii / 4 + other);
};

// tool の説明を「preset ごとに固定」と「利用者ごとに変わる」の 2 つに分ける (varies.description_from)。
// spec 全体が差し替わる tool (overridden) は説明全体を利用者ごととする
export const splitToolDescription = (tool: PreviewTool): { fixed: string; varies: string } => {
	if (tool.varies?.overridden) return { fixed: '', varies: tool.description };
	const from = tool.varies?.description_from;
	if (typeof from !== 'number') return { fixed: tool.description, varies: '' };
	return { fixed: tool.description.slice(0, from), varies: tool.description.slice(from) };
};

export const toolVaries = (tool: PreviewTool): boolean =>
	!!(
		tool.varies &&
		(tool.varies.overridden ||
			typeof tool.varies.description_from === 'number' ||
			(tool.varies.params ?? []).length > 0)
	);
