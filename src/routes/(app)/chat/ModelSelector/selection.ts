/**
 * チャットの選択欄(左上のモデル)に置く id の規則。
 *
 * 選べるのは利用者のモデル一覧($models)にある preset だけ。ただし開いているチャットに
 * 記録されたモデル(chat.models[0])は、一覧に無くてもそのチャットを開いている間だけ
 * 選択欄に残す — 利用者が読めない preset(サブエージェント用など)のチャットを開いたとき、
 * 何で書かれたチャットかが選択欄から消えないように。一覧には足さないので、ほかの
 * チャットでは選べない。そのモデルでは送信できない(isUnavailableModel で判定する)。
 */

/**
 * 選択欄の id を整える。1 モデルだけに切り詰め、一覧に無い id は '' に落とす。
 * recordedModelId(開いているチャットに記録されたモデル)は一覧に無くても残す。
 */
export const normalizeSelectedModels = (
	selected: string[],
	availableIds: string[],
	recordedModelId: string | null
): string[] =>
	selected
		.slice(0, 1)
		.map((id) => (availableIds.includes(id) || (id !== '' && id === recordedModelId) ? id : ''));

/** 選択欄にあるが一覧に無い(= この利用者は送れない)モデルか。未選択('')は false。 */
export const isUnavailableModel = (id: string | undefined, availableIds: string[]): boolean =>
	!!id && !availableIds.includes(id);
