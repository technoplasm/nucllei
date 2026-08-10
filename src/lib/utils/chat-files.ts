// llens(#69): 「このチャットのファイル」の収集ロジック。
// ChatFiles.svelte(右ペイン一覧)と Navbar(アイコンのバッジ数)で共有する。
//
// 収集元は 3 系統を file.id で dedupe:
// - chat レベルの files(アップロード添付。chatContent.files)
// - history 全メッセージの files(アップロード画像に加え、run_python 等の tool が
//   chat:message:files で添付した生成ファイル — type 'file' もここに来る)
// - 本文中のインライン参照(`![plot](/api/v1/files/{id}/content)` 等)。
//   backend の「関連チャット」導出(chats.py の chat JSON LIKE)は本文参照でも
//   ヒットするため、ここを拾わないと「ワークスペースの Files からは関連チャットに
//   見えるのに、当のチャットのペインは空」という非対称になる(添付永続化を逃した
//   生成物はモデルが本文に貼った URL だけが痕跡になる)。
// 旧チャットの data: URL 画像は id が無いため対象外(従来どおりメッセージ内でのみ表示)。
import type { ChatFile, ChatHistory } from '$lib/types/chat';

export type ChatFileListItem = {
	id: string;
	type: 'file' | 'image';
	name: string;
	size: number | null;
};

const toItem = (file: ChatFile): ChatFileListItem | null => {
	// type は 'file' | 'image' のみ対象(想定外の将来型は無視)。id 無しは開けないので対象外
	if (!file?.id || (file.type !== 'file' && file.type !== 'image')) {
		return null;
	}
	// サムネイル判定: アップロード画像は type 'image'、tool 生成の画像は
	// type 'file' + content_type image/* で来る — どちらも画像として描く
	const contentType = String(file.file?.meta?.content_type ?? '');
	return {
		id: file.id,
		type: file.type === 'image' || contentType.startsWith('image/') ? 'image' : 'file',
		name: file.name ?? file.file?.meta?.name ?? file.file?.filename ?? file.id,
		size: file.size ?? file.file?.meta?.size ?? null
	};
};

// 本文中のファイル参照: markdown の画像/リンク形(`![alt](/api/v1/files/<uuid>...)`)。
// `!` 付きは画像として扱い、alt/リンクテキストを表示名に使う。パス必須(裸の UUID は
// tool 結果などに任意に現れるため対象外 — 誤爆で無関係な id を拾わない)。
const INLINE_FILE_RE =
	/(!?)\[([^\]]*)\]\(\/api\/v1\/files\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:\/[^)\s]*)?\)/g;

export const collectChatFiles = (
	chatFiles: ChatFile[] | null | undefined,
	history: ChatHistory | null | undefined
): ChatFileListItem[] => {
	const items: ChatFileListItem[] = [];
	const seen = new Set<string>();

	const push = (file: ChatFile) => {
		const item = toItem(file);
		if (!item || seen.has(item.id)) {
			return;
		}
		seen.add(item.id);
		items.push(item);
	};

	const messages = Object.values(history?.messages ?? {});

	for (const file of chatFiles ?? []) {
		push(file);
	}
	for (const message of messages) {
		for (const file of message?.files ?? []) {
			push(file);
		}
	}
	// インライン参照は files 配列の後(dedupe でメタデータ付きの entry が勝つ)
	for (const message of messages) {
		for (const match of (message?.content ?? '').matchAll(INLINE_FILE_RE)) {
			const [, bang, label, id] = match;
			if (seen.has(id)) {
				continue;
			}
			seen.add(id);
			items.push({
				id,
				type: bang ? 'image' : 'file',
				name: label || id,
				size: null
			});
		}
	}

	return items;
};
