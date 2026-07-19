<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Switch from './Switch.svelte';

	let {
		valvesSpec = null,
		valves = $bindable({}),
		onChange = undefined
	}: {
		valvesSpec?: any;
		valves?: Record<string, any>;
		onChange?: () => void;
	} = $props();
</script>

{#if valvesSpec && Object.keys(valvesSpec?.properties ?? {}).length}
	{#each Object.keys(valvesSpec.properties) as property}
		<div class=" py-0.5 w-full justify-between">
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{valvesSpec.properties[property].title}

					{#if (valvesSpec?.required ?? []).includes(property)}
						<span class=" text-content-muted">*required</span>
					{/if}
				</div>

				<button
					class="p-1 px-3 text-xs flex rounded-sm transition"
					type="button"
					onclick={() => {
						valves[property] =
							(valves[property] ?? null) === null
								? (valvesSpec.properties[property]?.default ?? '')
								: null;

						onChange?.();
					}}
				>
					{#if (valves[property] ?? null) === null}
						<span class="ml-2 self-center">
							{#if (valvesSpec?.required ?? []).includes(property)}
								{$i18n.t('None')}
							{:else}
								{$i18n.t('Default')}
							{/if}
						</span>
					{:else}
						<span class="ml-2 self-center"> {$i18n.t('Custom')} </span>
					{/if}
				</button>
			</div>

			{#if (valves[property] ?? null) !== null}
				<!-- {valves[property]} -->
				<div class="flex mt-0.5 mb-1.5 space-x-2">
					<div class=" flex-1">
						{#if valvesSpec.properties[property]?.enum ?? null}
							<select
								class="w-full rounded-lg py-2 px-4 text-sm text-content dark:bg-gray-850 outline-hidden border border-gray-100 dark:border-gray-850"
								bind:value={valves[property]}
								onchange={() => {
									onChange?.();
								}}
							>
								{#each valvesSpec.properties[property].enum as option}
									<option value={option} selected={option === valves[property]}>
										{option}
									</option>
								{/each}
							</select>
						{:else if (valvesSpec.properties[property]?.type ?? null) === 'boolean'}
							<div class="flex justify-between items-center">
								<div class="text-xs text-content-muted">
									{valves[property] ? 'Enabled' : 'Disabled'}
								</div>

								<div class=" pr-2">
									<Switch
										bind:state={valves[property]}
										onChange={() => {
											onChange?.();
										}}
									/>
								</div>
							</div>
						{:else if (valvesSpec.properties[property]?.type ?? null) !== 'string'}
							<input
								class="w-full rounded-lg py-2 px-4 text-sm text-content dark:bg-gray-850 outline-hidden border border-gray-100 dark:border-gray-850"
								type="text"
								placeholder={valvesSpec.properties[property].title}
								bind:value={valves[property]}
								autocomplete="off"
								required
								onchange={() => {
									onChange?.();
								}}
							/>
						{:else}
							<textarea
								class="w-full rounded-lg py-2 px-4 text-sm text-content dark:bg-gray-850 outline-hidden border border-gray-100 dark:border-gray-850"
								placeholder={valvesSpec.properties[property].title}
								bind:value={valves[property]}
								autocomplete="off"
								required
								onchange={() => {
									onChange?.();
								}}
							></textarea>
						{/if}
					</div>
				</div>
			{/if}

			{#if (valvesSpec.properties[property]?.description ?? null) !== null}
				<div class="text-xs text-content-muted">
					{valvesSpec.properties[property].description}
				</div>
			{/if}
		</div>
	{/each}
{:else}
	<div class="text-xs">No valves</div>
{/if}
