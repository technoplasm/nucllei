<script lang="ts">
	import { models, showSettings, user, mobile, config } from '$lib/stores';
	import { onMount, tick, getContext } from 'svelte';
	import Selector from './ModelSelector/Selector.svelte';
	import Tooltip from '../common/Tooltip.svelte';

	const i18n = getContext('i18n');

	export let selectedModels = [''];
	export let disabled = false;

	$: if (selectedModels.length > 0 && $models.length > 0) {
		selectedModels = selectedModels.map((model) =>
			$models.map((m) => m.id).includes(model) ? model : ''
		);
	}
</script>

<div class="flex flex-col w-full items-start">
	{#each selectedModels as selectedModel, selectedModelIdx}
		<div class="flex w-full max-w-fit">
			<div class="overflow-hidden w-full">
				<div class="mr-1 max-w-full">
					<Selector
						id={`${selectedModelIdx}`}
						placeholder={$i18n.t('Select a model')}
						items={$models.map((model) => ({
							value: model.id,
							label: model.name,
							model: model
						}))}
						showTemporaryChatControl={$user?.role === 'user'
							? ($user?.permissions?.chat?.temporary ?? true) &&
								!($user?.permissions?.chat?.temporary_enforced ?? false)
							: true}
						bind:value={selectedModel}
					/>
				</div>
			</div>

			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true)}
				{#if selectedModelIdx === 0}
					<div
						class="  self-center mx-1 disabled:text-gray-600 disabled:hover:text-gray-600 -translate-y-[0.5px]"
					>
						<Tooltip content={$i18n.t('Add Model')}>
							<button
								class=" "
								{disabled}
								on:click={() => {
									selectedModels = [...selectedModels, ''];
								}}
								aria-label="Add Model"
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2"
									stroke="currentColor"
									class="size-3.5"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M12 6v12m6-6H6" />
								</svg>
							</button>
						</Tooltip>
					</div>
				{:else}
					<div
						class="  self-center mx-1 disabled:text-gray-600 disabled:hover:text-gray-600 -translate-y-[0.5px]"
					>
						<Tooltip content={$i18n.t('Remove Model')}>
							<button
								{disabled}
								on:click={() => {
									selectedModels.splice(selectedModelIdx, 1);
									selectedModels = selectedModels;
								}}
								aria-label="Remove Model"
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2"
									stroke="currentColor"
									class="size-3"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M19.5 12h-15" />
								</svg>
							</button>
						</Tooltip>
					</div>
				{/if}
			{/if}
		</div>
	{/each}
</div>

