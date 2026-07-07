// Skill の /command 判定ヘルパ(issue #35)。
// サーバ(middleware)は全 user メッセージの「先頭行の行頭」だけを発火対象にするため、
// フロントの picker 表示条件・ハイライト述語もこの正規表現に完全一致させる。
// SKILL_COMMAND_PATTERN はサーバ側の同名定数と同一に保つこと(片方だけ変えない)。
export const SKILL_COMMAND_PATTERN = /^\/([a-zA-Z0-9_-]+)/;

// llens(#43): skill の可読キーは id 一本(旧 command)。/command 一致 = id 一致。
type SkillLike = { id: string };

// text の先頭行の行頭がパターンに一致すれば command 文字列(= skill id、先頭 `/` なし)を返す。
// 先頭行以外・行頭以外の `/foo` は対象外(= サーバの素通しと一致)。
export const matchSkillCommand = (text: string): string | null => {
	if (!text) {
		return null;
	}
	const firstLine = text.split('\n')[0];
	const match = firstLine.match(SKILL_COMMAND_PATTERN);
	return match ? match[1] : null;
};

// matchSkillCommand の結果が skillList(store の中身、null 許容)の id に
// 完全一致すればその skill を返す。無効(未定義)なら null = サーバ素通しと一致。
export const findSkillByCommand = <T extends SkillLike>(
	skillList: T[] | null | undefined,
	text: string
): T | null => {
	const command = matchSkillCommand(text);
	if (!command || !skillList) {
		return null;
	}
	return skillList.find((s) => s.id === command) ?? null;
};
