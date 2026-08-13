<script lang="ts">
	// ResponseMessage の本文編集フォーム。編集バッファはここに閉じ、
	// 確定・複製保存・キャンセルは callback prop で親に返す。
	import { onMount, tick } from 'svelte';
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let {
		messageId,
		content,
		onSave,
		onSaveAsCopy,
		onCancel
	}: {
		messageId: string;
		content: string;
		onSave: (content: string) => void;
		onSaveAsCopy: (content: string) => void;
		onCancel: () => void;
	} = $props();

	// content は編集開始時のスナップショットとして意図的に初期値で捕捉する
	// svelte-ignore state_referenced_locally
	let editedContent = $state(content);
	let textAreaElement: HTMLTextAreaElement | null = $state(null);

	const resizeTextArea = (el: HTMLTextAreaElement) => {
		el.style.height = '';
		el.style.height = `${el.scrollHeight}px`;
	};

	onMount(async () => {
		await tick();
		if (textAreaElement) {
			resizeTextArea(textAreaElement);
		}
	});
</script>

<div class="w-full bg-gray-50 dark:bg-gray-800 rounded-3xl px-5 py-3 my-2">
	<textarea
		id="message-edit-{messageId}"
		bind:this={textAreaElement}
		class=" bg-transparent outline-hidden w-full resize-none"
		bind:value={editedContent}
		oninput={(e) => {
			resizeTextArea(e.currentTarget);
		}}
		onkeydown={(e) => {
			if (e.key === 'Escape') {
				document.getElementById('close-edit-message-button')?.click();
			}

			const isCmdOrCtrlPressed = e.metaKey || e.ctrlKey;
			const isEnterPressed = e.key === 'Enter';

			if (isCmdOrCtrlPressed && isEnterPressed) {
				document.getElementById('confirm-edit-message-button')?.click();
			}
		}}
	></textarea>

	<div class=" mt-2 mb-1 flex justify-between text-sm font-medium">
		<div>
			<button
				id="save-new-message-button"
				class=" px-4 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-800 dark:hover:bg-gray-700 border border-gray-100 dark:border-gray-700 text-content rounded-3xl"
				onclick={() => {
					onSaveAsCopy(editedContent);
				}}
			>
				{$i18n.t('Save As Copy')}
			</button>
		</div>

		<div class="flex space-x-1.5">
			<button
				id="close-edit-message-button"
				class="px-4 py-2 bg-white dark:bg-gray-900 hover:bg-gray-100 dark:hover:bg-gray-850 text-content rounded-3xl"
				onclick={() => {
					onCancel();
				}}
			>
				{$i18n.t('Cancel')}
			</button>

			<button
				id="confirm-edit-message-button"
				class=" px-4 py-2 bg-gray-900 text-white hover:bg-gray-850 dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-3xl"
				onclick={() => {
					onSave(editedContent);
				}}
			>
				{$i18n.t('Save')}
			</button>
		</div>
	</div>
</div>
