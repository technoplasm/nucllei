<script lang="ts">
	import { getContext } from 'svelte';
	import dayjs from 'dayjs';
	import { formatFileSize } from '$lib/utils';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { user } from '$lib/stores';

	const i18n = getContext('i18n');

	import Modal from './Modal.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
	import Tooltip from './Tooltip.svelte';

	import { getFileById } from '$lib/apis/files';

	// llens(#69): /files 管理ページ用の拡張(すべて省略可 — チャット側の既存利用は不変)。
	// download=true でダウンロードリンク、onDelete/onShare はフッターのボタンから callback。
	let {
		item,
		show = $bindable(false),
		download = false,
		onDelete = null,
		onShare = null
	}: {
		item: any;
		show?: boolean;
		download?: boolean;
		onDelete?: null | (() => void);
		onShare?: null | (() => void);
	} = $props();

	// llens: 既定タブは preview(PDF=原本、テキスト系=md レンダリング)。content は md ソース。
	let selectedTab = $state<'content' | 'preview'>('preview');

	// llens(#69): 画像 / PDF は元データをそのままプレビュー(content 取得は同一 URL)。
	const fileId = $derived(item?.file?.id ?? item?.id);
	const contentType = $derived(item?.file?.meta?.content_type ?? item?.meta?.content_type ?? '');
	const isImage = $derived(contentType.startsWith('image/'));
	const isPdf = $derived(contentType === 'application/pdf');
	// 音声は原本をその場で再生できるようにする(抽出テキストがあればその上に出す)。
	// 音声入力の通し録音を利用者が自分で聞き直せることが、文字起こしの誤りの一番確実な確認手段
	const isAudio = $derived(contentType.startsWith('audio/'));

	// ブラウザの MediaRecorder が作る webm は長さを持たず、duration が Infinity になって
	// シークバーが効かない。末尾へ一度シークさせると長さが確定するので、確定したら先頭へ戻す
	const settleAudioDuration = (event: Event) => {
		const audio = event.currentTarget as HTMLAudioElement;
		if (audio.duration !== Infinity) return;

		audio.addEventListener(
			'durationchange',
			() => {
				audio.currentTime = 0;
			},
			{ once: true }
		);
		audio.currentTime = Number.MAX_SAFE_INTEGER;
	};

	// llens: チャット側の item.file はアップロード時のスナップショットで古いことがある
	// (後から抽出が走った・Attachment 設定が変わった等)。開くたびに API から取り直して
	// data(抽出 content / status)を最新化する。取得失敗時はスナップショットのまま。
	// #94: 削除済み file は owner には縮小 marker {id, deleted_at} で返る(filename なし)。
	// owner 以外(元共有者含む)は 404 — fetchNotFound で「削除済み」表示に落とす。
	// 404 以外の失敗(ネットワーク等)は従来どおりスナップショット表示のまま。
	let freshFile = $state<any>(null);
	let fetchNotFound = $state(false);
	$effect(() => {
		if (!show) {
			return;
		}
		selectedTab = 'preview';
		freshFile = null;
		fetchNotFound = false;
		if (fileId) {
			getFileById(fileId)
				.then((f) => (freshFile = f))
				.catch((err) => {
					if (err?.status === 404) {
						fetchNotFound = true;
					}
				});
		}
	});

	// llens(#52): 添付ファイルは「モデルにどう見えるか(= 抽出された content)」を見せる。
	// content は md 前提。コンテンツ=md のソース(code block)、プレビュー=md をレンダリング。
	// 状態は attachment が制御(success/failed)。未抽出(none)= status も content も無い。
	const data = $derived(freshFile?.data ?? item?.file?.data ?? {});
	const content = $derived(data?.content ?? '');
	const status = $derived(data?.status); // 'success' | 'failed' | undefined
	const extraction = $derived(
		status === 'failed'
			? 'failed'
			: content.trim() === '' && status !== 'success'
				? 'none'
				: 'success'
	);

	// file の ACL 意味論: null=非公開 / read.public=全体公開 / それ以外=グループ共有
	const accessControl = $derived(item?.file?.access_control ?? null);
	const visibility = $derived(
		accessControl === null ? 'private' : accessControl?.read?.public ? 'public' : 'shared'
	);

	// llens(#94): soft delete 済み file の表示。非 admin には実体残存を見せない —
	// 縮小レスポンスに data が無いのに snapshot へフォールバックして中身を出さないよう、
	// deleted_at が立っていたらプレビュー・fallback を丸ごと抑止する。
	// 判定は refetch 結果だけでなく呼び出し元が渡した item.file にも効かせる
	// (refetch 解決までの一瞬に snapshot 中身が見えるのを防ぐ)。
	// refetch が 404(purge 済み共有 file 等)なら snapshot fallback もせず削除済み表示。
	// admin は purged でなければ実体を閲覧できる(これが #94 の「admin の閲覧」仕様)。
	const isAdmin = $derived($user?.role === 'admin');
	const fileState = $derived(freshFile ?? item?.file);
	const fileDeleted = $derived(fetchNotFound || !!fileState?.deleted_at);
	const filePurged = $derived(!!fileState?.purged_at);
	const contentHidden = $derived(fetchNotFound || (fileDeleted && (!isAdmin || filePurged)));

	const formatTimestamp = (ts: number | null | undefined) =>
		dayjs((ts ?? 0) * 1000).format('YYYY-MM-DD HH:mm');
</script>

<Modal bind:show size="lg">
	<div class="font-primary px-6 py-5 w-full flex flex-col justify-center text-content-muted">
		<div class="pb-2">
			<!-- llens: 縦スペース節約 — size • content-type と抽出バッジはファイル名と同じ行に -->
			<div class="flex items-start justify-between gap-3">
				<div class="flex items-baseline gap-2 min-w-0">
					<div class="font-medium text-lg text-content line-clamp-1">
						{item?.name ?? 'File'}
					</div>

					<div class="shrink-0 flex items-center gap-1.5 text-sm text-content-muted">
						{#if item?.size}
							<span>{formatFileSize(item.size)}</span>
						{/if}
						{#if (isImage || isPdf || isAudio) && contentType}
							{#if item?.size}<span>•</span>{/if}
							<span>{contentType}</span>
						{/if}
						{#if !isImage && !contentHidden}
							{#if extraction === 'failed'}
								<span class="text-red-500 font-medium">・{$i18n.t('Extraction failed')}</span>
							{:else if extraction === 'none'}
								<span class="text-content-subtle font-medium">・{$i18n.t('Not extracted')}</span>
							{/if}
						{/if}
					</div>
				</div>
				<button aria-label={$i18n.t('Close')} onclick={() => (show = false)}>
					<span class="i-lucide-x size-3.5 block"></span>
				</button>
			</div>

			{#if fileState?.deleted_at && isAdmin}
				<!-- #94: admin には削除メタを見せる(deleted_by の名前解決はせず id 素通し) -->
				<div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
					<span
						class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-red-500/10 text-red-600 dark:text-red-400 font-medium"
					>
						<span class="i-lucide-trash-2 size-3.5 block"></span>
						{$i18n.t('Deleted')}
					</span>
					<span class="text-content-muted">
						{formatTimestamp(fileState?.deleted_at)} • {fileState?.deleted_by}
					</span>
					{#if filePurged}
						<span class="text-content-muted">
							/ {$i18n.t('Purged')}: {formatTimestamp(fileState?.purged_at)} • {fileState?.purged_by}
						</span>
					{/if}
				</div>
			{/if}

			{#if !contentHidden && !isImage && !isPdf && !(isAudio && extraction === 'none')}
				<div class="text-sm text-content-muted mt-0.5">
					{$i18n.t(
						'Previewing the text extracted from the original data. Formatting and content may differ.'
					)}
				</div>
			{/if}

			{#if !contentHidden && !isImage && extraction === 'success'}
				<div
					class="flex items-center gap-3 mt-3 text-sm border-b border-gray-100 dark:border-gray-850"
				>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'preview'
							? 'border-gray-800 dark:border-gray-100 text-content font-medium'
							: 'border-transparent'}"
						onclick={() => (selectedTab = 'preview')}
					>
						{$i18n.t('Preview')}
					</button>
					<button
						class="pb-1 -mb-px border-b-2 {selectedTab === 'content'
							? 'border-gray-800 dark:border-gray-100 text-content font-medium'
							: 'border-transparent'}"
						onclick={() => (selectedTab = 'content')}
					>
						{$i18n.t('Content')}
					</button>
				</div>
			{/if}
		</div>

		<div class="max-h-[75vh] overflow-auto mt-2">
			{#if isAudio && !contentHidden}
				<audio
					controls
					preload="metadata"
					src={`${WEBUI_API_BASE_URL}/files/${fileId}/content`}
					class="w-full mb-3"
					onloadedmetadata={settleAudioDuration}
				></audio>
			{/if}

			{#if contentHidden}
				<!-- #94: 非 admin には削除済みの事実だけ、admin でも purged は実体が無い -->
				<div
					class="flex flex-col items-center gap-2 py-10 text-sm text-red-600 dark:text-red-400 font-medium"
				>
					<span class="{filePurged ? 'i-lucide-file-x' : 'i-lucide-trash-2'} size-6 block"></span>
					{filePurged && isAdmin
						? $i18n.t('File content has been purged')
						: $i18n.t('This file has been deleted')}
				</div>
			{:else if isImage}
				<img
					src={`${WEBUI_API_BASE_URL}/files/${fileId}/content`}
					alt={item?.name ?? 'Image'}
					class="max-h-[70vh] w-auto mx-auto rounded-lg"
				/>
			{:else if isPdf && (extraction !== 'success' || selectedTab === 'preview')}
				<iframe
					src={`${WEBUI_API_BASE_URL}/files/${fileId}/content`}
					title={item?.name ?? 'PDF'}
					class="w-full h-[70vh] rounded-lg border border-line!"
				></iframe>
			{:else if extraction === 'failed'}
				<div class="text-sm whitespace-pre-wrap text-red-500 dark:text-red-400">
					{content}
				</div>
			{:else if extraction === 'none'}
				<div class="text-sm text-content-subtle">
					{$i18n.t('This file has not been extracted to text yet.')}
				</div>
			{:else if selectedTab === 'content'}
				<pre
					class="text-xs whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-850 rounded-lg p-3 overflow-auto"><code
						>{content}</code
					></pre>
			{:else}
				<div class="markdown-prose text-sm">
					<Markdown id={`file-preview-${item?.id ?? ''}`} {content} />
				</div>
			{/if}
		</div>

		{#if download || onDelete || onShare}
			<div class="flex justify-end items-center gap-0.5 mt-3">
				{#if onShare && !fileDeleted}
					<Tooltip content={$i18n.t('Access Control')}>
						<button
							class="flex items-center gap-1.5 px-3 py-1.5 text-sm text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl transition"
							type="button"
							onclick={() => onShare?.()}
						>
							{#if visibility === 'public'}
								<span class="i-lucide-globe size-4 block"></span>
							{:else if visibility === 'shared'}
								<span class="i-lucide-users size-4 block"></span>
							{:else}
								<span class="i-lucide-lock size-4 block"></span>
							{/if}
							<span>{$i18n.t('Share')}</span>
						</button>
					</Tooltip>
				{/if}

				{#if download && fileId && !contentHidden}
					<a
						class="flex items-center gap-1.5 px-3 py-1.5 text-sm text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl transition"
						href={`${WEBUI_API_BASE_URL}/files/${fileId}/content?attachment=true`}
					>
						<span class="i-lucide-download size-4 block"></span>
						<span>{$i18n.t('Download')}</span>
					</a>
				{/if}

				{#if onDelete && !fileDeleted}
					<button
						class="flex items-center gap-1.5 px-3 py-1.5 text-sm text-content dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl transition"
						type="button"
						onclick={() => onDelete?.()}
					>
						<span class="i-lucide-trash-2 size-4 block"></span>
						<span>{$i18n.t('Delete')}</span>
					</button>
				{/if}
			</div>
		{/if}
	</div>
</Modal>
