<script lang="ts">
	// テキスト選択のフローティングメニュー(Ask / Explain)。
	// 位置決めと開閉は親(ContentRenderer)が DOM 経由で制御し、
	// 閉じるときは bind:this の closeHandler を呼ぶ。
	import { toast } from 'svelte-sonner';

	import { getContext, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { chatCompletion } from '$lib/apis/openai';

	import Markdown from '../Messages/Markdown.svelte';
	import PendingIndicator from '../Messages/PendingIndicator.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	type SimpleMessage = { role: string; content: string };

	let {
		id = '',
		model = null,
		messages = [],
		onAdd = () => {}
	}: {
		id?: string;
		model?: string | null;
		messages?: SimpleMessage[];
		onAdd?: (payload: { modelId: string; parentId: string; messages: SimpleMessage[] }) => void;
	} = $props();

	let floatingInput = $state(false);

	let selectedText = $state('');
	let floatingInputValue = $state('');

	let prompt = $state('');
	let responseContent: string | null = $state(null);
	let responseDone = $state(false);

	const autoScroll = async () => {
		// Scroll to bottom only if the scroll is at the bottom give 50px buffer
		const responseContainer = document.getElementById('response-container');
		if (
			responseContainer &&
			responseContainer.scrollHeight - responseContainer.clientHeight <=
				responseContainer.scrollTop + 50
		) {
			responseContainer.scrollTop = responseContainer.scrollHeight;
		}
	};

	// Ask / Explain 共通のストリーミング本体(元は 2 箇所に丸ごと重複していた)
	const streamResponse = async (userPrompt: string) => {
		const modelId = model;
		if (!modelId) {
			toast.error('Model not selected');
			return;
		}

		prompt = userPrompt;
		responseContent = '';

		const [res] = await chatCompletion({
			model: modelId,
			messages: [
				...messages,
				{
					role: 'user',
					content: userPrompt
				}
			].map((message) => ({
				role: message.role,
				content: message.content
			})),
			stream: true // Enable streaming
		});

		if (!res?.ok || !res.body) {
			toast.error('An error occurred while fetching the explanation');
			return;
		}

		const reader = res.body.getReader();
		const decoder = new TextDecoder();

		while (true) {
			// Read data chunks from the response stream
			const { done, value } = await reader.read();
			if (done) {
				break;
			}

			// Decode the received chunk
			const chunk = decoder.decode(value, { stream: true });

			// Process lines within the chunk
			const lines = chunk.split('\n').filter((line) => line.trim() !== '');

			for (const line of lines) {
				if (!line.startsWith('data: ')) {
					continue;
				}

				if (line.startsWith('data: [DONE]')) {
					responseDone = true;

					await tick();
					autoScroll();
					continue;
				}

				// Parse the JSON chunk
				try {
					const data = JSON.parse(line.slice(6));

					// Append the `content` field from the "choices" object
					if (data.choices && data.choices[0]?.delta?.content) {
						responseContent += data.choices[0].delta.content;

						autoScroll();
					}
				} catch (e) {
					console.error(e);
				}
			}
		}
	};

	const askHandler = async () => {
		const input = floatingInputValue;
		floatingInputValue = '';

		await streamResponse(`${input}\n\`\`\`\n${selectedText}\n\`\`\``);
	};

	const explainHandler = async () => {
		const explainText = $i18n.t('Explain this section to me in more detail');

		await streamResponse(`${explainText}\n\n\`\`\`\n${selectedText}\n\`\`\``);
	};

	const addHandler = () => {
		if (!model) {
			return;
		}

		onAdd({
			modelId: model,
			parentId: id,
			messages: [
				{
					role: 'user',
					content: prompt
				},
				{
					role: 'assistant',
					content: responseContent ?? ''
				}
			]
		});
	};

	export const closeHandler = () => {
		responseContent = null;
		responseDone = false;
		floatingInput = false;
		floatingInputValue = '';
	};
</script>

<div
	id={`floating-buttons-${id}`}
	class="absolute rounded-lg mt-1 text-xs z-9999"
	style="display: none"
>
	{#if responseContent === null}
		{#if !floatingInput}
			<div
				class="flex flex-row gap-0.5 shrink-0 p-1 bg-white dark:bg-gray-850 text-content text-medium rounded-lg shadow-xl"
			>
				<button
					class="px-1 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-sm flex items-center gap-1 min-w-fit"
					onclick={async () => {
						selectedText = window.getSelection()?.toString() ?? '';
						floatingInput = true;

						await tick();
						setTimeout(() => {
							document.getElementById('floating-message-input')?.focus();
						}, 0);
					}}
				>
					<span class="i-lucide-message-circle size-3 shrink-0 block"></span>

					<div class="shrink-0">{$i18n.t('Ask')}</div>
				</button>
				<button
					class="px-1 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-sm flex items-center gap-1 min-w-fit"
					onclick={() => {
						selectedText = window.getSelection()?.toString() ?? '';
						explainHandler();
					}}
				>
					<span class="i-lucide-lightbulb size-3 shrink-0 block"></span>

					<div class="shrink-0">{$i18n.t('Explain')}</div>
				</button>
			</div>
		{:else}
			<div
				class="py-1 flex text-content bg-gray-50 dark:bg-gray-800 border border-gray-100 dark:border-gray-850 w-72 rounded-full shadow-xl"
			>
				<input
					type="text"
					id="floating-message-input"
					class="ml-5 bg-transparent outline-hidden w-full flex-1 text-sm"
					placeholder={$i18n.t('Ask a question')}
					bind:value={floatingInputValue}
					onkeydown={(e) => {
						if (e.key === 'Enter') {
							askHandler();
						}
					}}
				/>

				<div class="ml-1 mr-2">
					<button
						class="{floatingInputValue !== ''
							? 'bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100 '
							: 'text-white bg-gray-200 dark:text-content-subtle dark:bg-gray-700 disabled'} rounded-full p-1.5 m-0.5 self-center"
						aria-label={$i18n.t('Send message')}
						onclick={() => {
							askHandler();
						}}
					>
						<span class="i-lucide-arrow-up size-4 block"></span>
					</button>
				</div>
			</div>
		{/if}
	{:else}
		<div class="bg-white dark:bg-gray-850 text-content rounded-xl shadow-xl w-80 max-w-full">
			<div
				class="bg-gray-50/50 dark:bg-gray-800 text-content text-medium rounded-xl px-3.5 py-3 w-full"
			>
				<div class="font-medium">
					<Markdown id={`${id}-float-prompt`} content={prompt} />
				</div>
			</div>

			<div class="bg-white dark:bg-gray-850 text-content text-medium rounded-xl px-3.5 py-3 w-full">
				<div class=" max-h-80 overflow-y-auto w-full markdown-prose-xs" id="response-container">
					{#if responseContent.trim() === ''}
						<PendingIndicator />
					{:else}
						<Markdown id={`${id}-float-response`} content={responseContent} />
					{/if}

					{#if responseDone}
						<div class="flex justify-end pt-3 text-sm font-medium">
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full"
								onclick={addHandler}
							>
								{$i18n.t('Add')}
							</button>
						</div>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</div>
