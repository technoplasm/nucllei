/**
 * サイドバー tree 用の「選択中チャットの子 agent session」state(#107 P3 W-C)。
 *
 * 選択中(/c/ で開いている)chat の直下の agent session を 1 親分だけ保持する。
 * 読み手はサイドバーの ChatItem(parentId が自分の id のときだけ tree を描く)。
 * 書き手は Chat.svelte(chat 切替時)と Chat/events.ts(親ターン完了時の再 fetch)。
 *
 * agent session を /c/ で観測しているときは「その親」の children を保持する —
 * tree は親項目の下に出続け、観測中の子がハイライトされる。
 *
 * running(走行中スピナー)は refresh 時に子ごとの getTaskIdsByChatId で判定する
 * (子は少数の前提。live 追従はしない — 親ターン完了・観測 done の再 fetch で収束)。
 */
import { getChatChildren, type ChatChildItem } from '$lib/apis/chats';
import { getTaskIdsByChatId } from '$lib/apis';

export type ChatChildEntry = ChatChildItem & {
	running: boolean;
};

const state = $state<{ parentId: string | null; entries: ChatChildEntry[] }>({
	parentId: null,
	entries: []
});

/** 遅延解決した古い fetch が新しい状態を上書きしないためのシーケンス。 */
let requestSeq = 0;

export const clearChatChildren = () => {
	requestSeq += 1;
	state.parentId = null;
	state.entries = [];
};

/** parentId の children を取得して差し替える。失敗・空 parentId はクリア扱い。 */
export const refreshChatChildren = async (parentId: string | null | undefined) => {
	const seq = ++requestSeq;
	if (!parentId) {
		state.parentId = null;
		state.entries = [];
		return;
	}

	const children = await getChatChildren(parentId).catch(() => null);
	if (seq !== requestSeq) {
		return;
	}
	if (!children) {
		state.parentId = null;
		state.entries = [];
		return;
	}

	const entries = await Promise.all(
		children.map(async (child) => {
			const taskRes = await getTaskIdsByChatId(child.id).catch(() => null);
			return { ...child, running: (taskRes?.task_ids ?? []).length > 0 };
		})
	);
	if (seq !== requestSeq) {
		return;
	}
	state.parentId = parentId;
	state.entries = entries;
};

/** 現在保持している親の children を再取得する(観測 done 時のスピナー更新等)。 */
export const refreshCurrentChatChildren = async () => {
	if (state.parentId) {
		await refreshChatChildren(state.parentId);
	}
};

/** 読み手(ChatItem)用のリアクティブな参照。プロパティ読み取りで購読される。 */
export const chatChildrenState = state;
