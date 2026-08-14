<script lang="ts">
	// llens(#110): システムが自動挿入した通知メッセージの表示。
	//
	// バックグラウンドタスクの配達通知(結果全文 + display + 添付画像)は BE が
	// user ロールで履歴へ直接書く(サーバ駆動再開 — meta.system_notice が根拠)。
	// UI でユーザー発言の吹き出しとして出すと「勝手にチャットを送った」ように
	// 見えるため、明示的にシステム挿入と分かる体裁(バッジ + 抑えた配色 +
	// 編集/削除なし)で描く。結果全文が載るため長文は折りたたみ、markdown で
	// レンダリングする(XSS 不変条件: Markdown 内部の DOMPurify.sanitize を通す —
	// tool 結果・skill 本文と同じ経路)。
	import { getContext } from 'svelte';
	import type { ChatHistory } from '$lib/types/chat';

	import Markdown from './Markdown.svelte';
	import Image from '$lib/components/common/Image.svelte';

	let { history, messageId }: { history: ChatHistory; messageId: string } = $props();

	const i18n = getContext('i18n');
	const message = $derived(history.messages[messageId]);
	const imageFiles = $derived((message?.files ?? []).filter((file) => file.type === 'image'));

	// 長文の折りたたみ: 畳んだ高さ(px)を超えるときだけ展開ボタンを出す。
	// overflow-hidden + max-height でも scrollHeight は全高を返すので測定に使える
	const COLLAPSED_MAX_PX = 240;
	let bodyElement = $state<HTMLDivElement | null>(null);
	let expanded = $state(false);
	let overflowing = $state(false);
	$effect(() => {
		// content の変化(配達直後の refetch 等)で再測定する
		void message?.content;
		if (bodyElement) {
			overflowing = bodyElement.scrollHeight > COLLAPSED_MAX_PX + 1;
		}
	});
</script>

<div class="flex w-full" id="message-{message?.id}">
	<div class="w-full py-1">
		<div
			class="w-full rounded-lg border border-line bg-surface-muted px-3 py-2 text-sm text-content-muted"
		>
			<div class="mb-1 flex items-center gap-1.5 text-xs font-medium text-content-subtle">
				<span class="i-lucide-cpu size-3.5 block"></span>
				<span>{$i18n.t('Inserted by the system')}</span>
			</div>
			<div
				bind:this={bodyElement}
				class="relative overflow-hidden break-words"
				style={expanded ? '' : `max-height: ${COLLAPSED_MAX_PX}px;`}
			>
				<Markdown id={`${message?.id}-system-notice`} content={message?.content ?? ''} />
				{#if !expanded && overflowing}
					<!-- 畳み中は下端をフェードさせ、続きがあることを示す -->
					<div
						class="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-linear-to-t from-surface-muted to-transparent"
					></div>
				{/if}
			</div>
			{#if imageFiles.length > 0}
				<div class="mt-2 flex flex-wrap gap-2">
					{#each imageFiles as file, i (file.id ?? file.url ?? i)}
						<Image src={file.url ?? ''} imageClassName="max-h-64 rounded-lg" />
					{/each}
				</div>
			{/if}
			{#if overflowing}
				<button
					class="mt-1.5 flex items-center gap-1 text-xs font-medium text-content-subtle hover:text-content transition"
					onclick={() => (expanded = !expanded)}
				>
					{#if expanded}
						<span class="i-lucide-chevron-up size-3.5 block"></span>
						<span>{$i18n.t('Collapse')}</span>
					{:else}
						<span class="i-lucide-chevron-down size-3.5 block"></span>
						<span>{$i18n.t('Expand')}</span>
					{/if}
				</button>
			{/if}
		</div>
	</div>
</div>
