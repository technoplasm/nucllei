<script lang="ts">
	// Markdown 本文の描画 + テキスト選択のフローティングボタン(Ask/Explain)。
	import { onDestroy, onMount, tick } from 'svelte';

	import { mobile } from '$lib/stores';
	import type { Model } from '$lib/stores';
	import { createMessagesList } from '$lib/utils';
	import type { ChatHistory } from '$lib/types/chat';

	import Markdown from './Markdown.svelte';
	import type { CitationScope } from './citations.svelte';
	import FloatingButtons from '../ContentRenderer/FloatingButtons.svelte';

	let {
		id,
		content,
		history,
		model = null,
		floatingButtons = true,
		citations = null,
		citationSlot = 0,
		onTaskClick = () => {},
		onAddMessages = () => {}
	}: {
		id: string;
		content: string;
		history: ChatHistory;
		model?: Model | null;
		floatingButtons?: boolean;
		/** メッセージ単位の引用の番号付け(#146 簡易版)と、その中でのこのブロックの番号 */
		citations?: CitationScope | null;
		citationSlot?: number;
		onTaskClick?: (payload: Record<string, unknown>) => void;
		onAddMessages?: (payload: {
			modelId: string;
			parentId: string;
			messages: { role: string; content: string }[];
		}) => void;
	} = $props();

	// 本文の `[file_id]` をリンクにする根拠: このチャット(history の全メッセージ)の添付
	// id → 表示名(#146 の簡易版)。添付の集合が変わらない限り同じ Map を返す
	// (Markdown 側は参照が変わると確定ブロックを lex し直す)
	let fileNamesCache: { key: string; map: Map<string, string> } | null = null;
	const fileNames = $derived.by(() => {
		const entries: [string, string][] = [];
		for (const message of Object.values(history?.messages ?? {})) {
			for (const file of message?.files ?? []) {
				if (!file?.id) continue;
				entries.push([file.id, file.name ?? file.file?.meta?.name ?? file.file?.filename ?? '']);
			}
		}
		const key = JSON.stringify(entries);
		if (fileNamesCache?.key !== key) fileNamesCache = { key, map: new Map(entries) };
		return fileNamesCache.map;
	});

	let contentContainerElement: HTMLDivElement | null = $state(null);
	let floatingButtonsElement: { closeHandler: () => void } | null = $state(null);

	const updateButtonPosition = (event: MouseEvent) => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (
			!contentContainerElement?.contains(event.target as Node) &&
			!buttonsContainerElement?.contains(event.target as Node)
		) {
			closeFloatingButtons();
			return;
		}

		setTimeout(async () => {
			await tick();

			if (!contentContainerElement?.contains(event.target as Node)) return;

			const selection = window.getSelection();

			if (selection && selection.toString().trim().length > 0) {
				const range = selection.getRangeAt(0);
				const rect = range.getBoundingClientRect();

				const parentRect = contentContainerElement.getBoundingClientRect();

				// Adjust based on parent rect
				const top = rect.bottom - parentRect.top;
				const left = rect.left - parentRect.left;

				if (buttonsContainerElement) {
					buttonsContainerElement.style.display = 'block';

					// Calculate space available on the right
					const spaceOnRight = parentRect.width - left;
					const halfScreenWidth = $mobile ? window.innerWidth / 2 : window.innerWidth / 3;

					if (spaceOnRight < halfScreenWidth) {
						const right = parentRect.right - rect.right;
						buttonsContainerElement.style.right = `${right}px`;
						buttonsContainerElement.style.left = 'auto'; // Reset left
					} else {
						// Enough space, position using 'left'
						buttonsContainerElement.style.left = `${left}px`;
						buttonsContainerElement.style.right = 'auto'; // Reset right
					}
					buttonsContainerElement.style.top = `${top + 5}px`; // +5 to add some spacing
				}
			} else {
				closeFloatingButtons();
			}
		}, 0);
	};

	const closeFloatingButtons = () => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (buttonsContainerElement) {
			buttonsContainerElement.style.display = 'none';
		}

		floatingButtonsElement?.closeHandler();
	};

	const keydownHandler = (e: KeyboardEvent) => {
		if (e.key === 'Escape') {
			closeFloatingButtons();
		}
	};

	onMount(() => {
		if (floatingButtons) {
			contentContainerElement?.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('keydown', keydownHandler);
		}
	});

	onDestroy(() => {
		if (floatingButtons) {
			contentContainerElement?.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('keydown', keydownHandler);
		}
	});
</script>

<div bind:this={contentContainerElement}>
	<Markdown {id} {content} {model} {fileNames} {citations} {citationSlot} {onTaskClick} />

	<!-- content div の中に置く(外に置くと space-y の親の下で :last-child 判定を壊し、
	     最後のブロックにも margin が付く — tailwind v4 の space-y は
	     `> :not(:last-child) { margin-bottom }` 実装)。absolute の位置基準は
	     祖先の relative(#response-content-container)のままなので挙動は不変。 -->
	{#if floatingButtons && model}
		<FloatingButtons
			bind:this={floatingButtonsElement}
			{id}
			model={model?.id}
			messages={createMessagesList(history, id)}
			onAdd={({ modelId, parentId, messages }) => {
				onAddMessages({ modelId, parentId, messages });
				closeFloatingButtons();
			}}
		/>
	{/if}
</div>
