/**
 * llens(#110): save-400 リカバリの接ぎ直し(契約 §M6 / 批評 F-3 裁定)。
 *
 * BE がバックグラウンドタスクの配達ターン(通知 user + 空 assistant)を chat へ
 * 直接 append すると、FE の手元 history は stale になり updateChatById が 400 で
 * 拒否される(BE 追加分を消す上書きを防ぐガード)。ここでは refetch した
 * サーバ history を土台に、ローカルで積んだ送信中の user+assistant 対
 * (= サーバに無いローカル末尾鎖)を新 currentId へ parentId 接ぎ直しして返す。
 *
 * - サーバに**ある** id はローカル版で上書き(ストリーム済み本文はローカルが新しい)。
 *   ただし childrenIds はサーバ側を優先に和集合 — BE が生やした通知子を消さない
 * - サーバに**無い**ローカル末尾鎖(currentId から親方向に辿った未保存分)は、
 *   先頭の parentId をサーバ currentId に差し替えて接ぐ
 * - 末尾鎖が無ければサーバ currentId を採る(配達ターンの先端 = より深い側)
 * - 末尾鎖が「枝分かれ」(再生成・発話の編集)なら接ぎ直さず元の親に残す。
 *   接ぎ直すのは会話の続き(手元の先端に新しい user を足した送信)だけ。
 *   別タブでサーバ側の会話が先へ進んでいるとき、古いタブで再生成した応答を
 *   サーバ先端(別の assistant)にぶら下げると、無関係な会話の子になってしまう
 */
import type { ChatHistory, ChatMessage } from '$lib/types/chat';

export const graftLocalTurnOntoServerHistory = (
	server: ChatHistory,
	local: ChatHistory
): ChatHistory => {
	const merged: ChatHistory = JSON.parse(JSON.stringify(server));

	// ローカル末尾鎖: currentId から親方向へ、サーバに無い message を集める
	// (deepest → shallow の順に貯め、最後に反転して oldest-first)
	const suffix: ChatMessage[] = [];
	const seen = new Set<string>();
	let id = local.currentId;
	while (id && !seen.has(id) && !server.messages[id]) {
		seen.add(id);
		const message = local.messages[id];
		if (!message) {
			break;
		}
		suffix.push(message);
		id = message.parentId;
	}
	suffix.reverse();
	const suffixIds = new Set(suffix.map((message) => message.id));

	// 共有 id の上書き: 本文・状態はローカルが新しい。childrenIds はサーバの並びを
	// 保った和集合(BE の通知子を温存)から末尾鎖の id を除く — 鎖は接ぎ直し先
	// (新 currentId)にだけぶら下げる(旧位置に幽霊参照を残さない)
	for (const message of Object.values(local.messages)) {
		const serverMessage = merged.messages[message.id];
		if (!serverMessage) {
			continue;
		}
		const childrenIds = [
			...serverMessage.childrenIds,
			...message.childrenIds.filter((childId) => !serverMessage.childrenIds.includes(childId))
		].filter((childId) => !suffixIds.has(childId));
		merged.messages[message.id] = {
			...JSON.parse(JSON.stringify(message)),
			childrenIds
		};
	}

	if (suffix.length > 0) {
		// 枝分かれでも元の親がサーバに無ければ(壊れた手元履歴)続きとして接ぐ
		const branchParentId = suffix[0].parentId;
		const keepBranch =
			!isContinuation(local, suffix[0], suffixIds) &&
			(branchParentId === null || !!merged.messages[branchParentId]);
		const graftParentId = keepBranch ? branchParentId : merged.currentId;
		const head = { ...JSON.parse(JSON.stringify(suffix[0])), parentId: graftParentId };
		merged.messages[head.id] = head;
		for (const message of suffix.slice(1)) {
			merged.messages[message.id] = JSON.parse(JSON.stringify(message));
		}
		const graftParent = graftParentId ? merged.messages[graftParentId] : undefined;
		if (graftParent && !graftParent.childrenIds.includes(head.id)) {
			graftParent.childrenIds = [...graftParent.childrenIds, head.id];
		}
		merged.currentId = local.currentId;
	}
	// 末尾鎖が無ければ merged.currentId(サーバの先端)をそのまま採る

	return merged;
};

/**
 * 末尾鎖の先頭 head が「会話の続き」か「枝分かれ」かの判定。
 *
 * 続き = 手元の先端に user を足した送信: head が user で、手元の親(root なら root 群)に
 * 兄弟が居ない。枝分かれ = 再生成(head が assistant — 既存 user の子に応答を足す)と
 * 発話の編集(head が user だが、編集前の兄弟が同じ親の下に居る)。
 */
const isContinuation = (local: ChatHistory, head: ChatMessage, suffixIds: Set<string>): boolean => {
	if (head.role !== 'user') {
		return false;
	}
	const siblingIds = head.parentId
		? (local.messages[head.parentId]?.childrenIds ?? [])
		: Object.values(local.messages)
				.filter((message) => message.parentId === null)
				.map((message) => message.id);
	return siblingIds.every((siblingId) => siblingId === head.id || suffixIds.has(siblingId));
};

/**
 * llens(#110): save-400 リカバリの refetch 失敗分岐用 — ローカルで積んだ送信対を
 * history から取り除き、送信前の先端へ currentId を巻き戻した形を返す。
 *
 * 再保存失敗分岐(サーバ形へ巻き戻し)と後始末を揃える: 送信対を残したまま
 * ok:false を返すと、呼び出し側の prompt 復元 → 再送で user メッセージが二重化する。
 *
 * 取り除く範囲:
 * - currentId の message(送信中の応答受け皿 assistant)
 * - その親が user で、子が送信中 assistant だけのとき(= この送信で作られた user)は
 *   それも取り除く。再生成(既存 user に assistant を足しただけ — user は保存済みで
 *   他の子を持つ)では user を残す
 */
export const stripLocalTurn = (local: ChatHistory): ChatHistory => {
	const rolled: ChatHistory = JSON.parse(JSON.stringify(local));
	const tipId = rolled.currentId;
	const tip = tipId ? rolled.messages[tipId] : undefined;
	if (!tipId || !tip) {
		return rolled;
	}

	const removeIds = new Set<string>([tipId]);
	let anchorId = tip.parentId;
	const parent = anchorId ? rolled.messages[anchorId] : undefined;
	if (
		parent &&
		parent.role === 'user' &&
		parent.childrenIds.every((childId) => removeIds.has(childId))
	) {
		removeIds.add(parent.id);
		anchorId = parent.parentId;
	}

	for (const id of removeIds) {
		delete rolled.messages[id];
	}
	const anchor = anchorId ? rolled.messages[anchorId] : undefined;
	if (anchor) {
		anchor.childrenIds = anchor.childrenIds.filter((childId) => !removeIds.has(childId));
	}
	rolled.currentId = anchor ? anchorId : null;
	return rolled;
};
