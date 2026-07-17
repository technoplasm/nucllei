<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { render } from '$lib/utils/markdown-it';
	import DOMPurify from 'dompurify';

	import { getContext, tick } from 'svelte';
	import { fade } from 'svelte/transition';

	import { user, models as _models, type Model } from '$lib/stores';
	import { sanitizeResponseContent } from '$lib/utils';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';

	import Suggestions from '$lib/components/chat/Suggestions.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import MessageInput from './MessageInput.svelte';

	const i18n = getContext('i18n');

	let {
		transparentBackground = false,
		createMessagePair,
		stopResponse,
		onSubmit = () => {},
		autoScroll = $bindable(false),
		atSelectedModel = $bindable(undefined),
		selectedModels,
		history,
		prompt = $bindable(''),
		files = $bindable([])
	}: {
		transparentBackground?: boolean;
		createMessagePair: Function;
		stopResponse: Function;
		onSubmit?: (prompt: string) => void;
		autoScroll?: boolean;
		atSelectedModel?: Model | undefined;
		selectedModels: string[];
		history: any;
		prompt?: string;
		files?: any[];
	} = $props();

	const models = $derived(selectedModels.map((id) => $_models.find((m) => m.id === id)));

	let selectedModelIdx = $state(0);

	$effect(() => {
		if (selectedModels.length > 0) {
			selectedModelIdx = models.length - 1;
		}
	});

	const selectSuggestionPrompt = async (p) => {
		let text = p;

		if (p.includes('{{CLIPBOARD}}')) {
			const clipboardText = await navigator.clipboard.readText().catch((err) => {
				toast.error($i18n.t('Failed to read clipboard contents'));
				return '{{CLIPBOARD}}';
			});

			text = p.replaceAll('{{CLIPBOARD}}', clipboardText);

			console.log('Clipboard text:', clipboardText, text);
		}

		prompt = text;

		console.log(prompt);
		await tick();

		const chatInputContainerElement = document.getElementById('chat-input-container');
		const chatInputElement = document.getElementById('chat-input');

		if (chatInputContainerElement) {
			chatInputContainerElement.scrollTop = chatInputContainerElement.scrollHeight;
		}

		await tick();
		if (chatInputElement) {
			chatInputElement.focus();
			chatInputElement.dispatchEvent(new Event('input'));
		}

		await tick();
	};
</script>

<div class="m-auto w-full max-w-6xl px-2 @2xl:px-20 translate-y-6 py-24 text-center">
	<div class="w-full text-3xl text-content text-center flex items-center gap-4 font-primary">
		<div class="w-full flex flex-col justify-center items-center">
			<div class="flex flex-row justify-center gap-3 @sm:gap-3.5 w-fit px-5">
				<div class="flex shrink-0 justify-center">
					<div class="flex -space-x-4 mb-0.5" in:fade={{ duration: 100 }}>
						{#each models as model, modelIdx}
							<button
								onclick={() => {
									selectedModelIdx = modelIdx;
								}}
							>
								<img
									crossorigin="anonymous"
									src={model?.info?.meta?.profile_image_url ??
										($i18n.language === 'dg-DG' ? `/doge.png` : DEFAULT_MODEL_IMAGE_URL)}
									class=" size-9 @sm:size-10 rounded-full border-[1px] border-gray-100 dark:border-none"
									alt="logo"
									draggable="false"
								/>
							</button>
						{/each}
					</div>
				</div>

				<div class=" text-3xl @sm:text-4xl line-clamp-1" in:fade={{ duration: 100 }}>
					{#if models[selectedModelIdx]?.name}
						{models[selectedModelIdx]?.name}
					{:else}
						{$i18n.t('Hello, {{name}}', { name: $user?.name })}
					{/if}
				</div>
			</div>

			<div class="flex mt-1 mb-2">
				<div in:fade={{ duration: 100, delay: 50 }}>
					{#if models[selectedModelIdx]?.info?.meta?.description ?? null}
						<Tooltip
							className=" w-fit"
							content={render(
								sanitizeResponseContent(models[selectedModelIdx]?.info?.meta?.description ?? '')
							)}
							placement="top"
						>
							<div
								class="mt-0.5 px-2 text-sm font-normal text-content-muted line-clamp-2 max-w-xl markdown"
							>
								<!-- llens(GHSA-gf5m-wcrh-7928): モデル説明の XSS 対策。marked 生成 HTML を
								     DOMPurify で無害化(javascript: リンク等を除去)。 -->
								{@html DOMPurify.sanitize(
									render(sanitizeResponseContent(models[selectedModelIdx]?.info?.meta?.description))
								)}
							</div>
						</Tooltip>

						{#if models[selectedModelIdx]?.info?.meta?.user}
							<div class="mt-0.5 text-sm font-normal text-content-subtle">
								By
								{models[selectedModelIdx]?.info?.meta?.user.name
									? models[selectedModelIdx]?.info?.meta?.user.name
									: `@${models[selectedModelIdx]?.info?.meta?.user.username}`}
							</div>
						{/if}
					{/if}
				</div>
			</div>

			<div class="text-base font-normal @md:max-w-3xl w-full py-3 {atSelectedModel ? 'mt-2' : ''}">
				<MessageInput
					{history}
					{selectedModels}
					bind:files
					bind:prompt
					bind:autoScroll
					bind:atSelectedModel
					{transparentBackground}
					{stopResponse}
					{createMessagePair}
					placeholder={$i18n.t('How can I help you today?')}
					{onSubmit}
				/>
			</div>
		</div>
	</div>
	<div class="mx-auto max-w-2xl font-primary" in:fade={{ duration: 200, delay: 200 }}>
		<div class="mx-5">
			<Suggestions
				suggestionPrompts={atSelectedModel?.info?.meta?.suggestion_prompts ??
					models[selectedModelIdx]?.info?.meta?.suggestion_prompts ??
					[]}
				inputValue={prompt}
				onSelect={(content) => {
					selectSuggestionPrompt(content);
				}}
			/>
		</div>
	</div>
</div>
