<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { models } from '$lib/stores';
	import { getContext, onMount, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import {
		getPipelineValves,
		getPipelineValvesSpec,
		updatePipelineValves,
		getPipelines,
		getModels,
		getPipelinesList,
		downloadPipeline,
		deletePipeline,
		uploadPipeline
	} from '$lib/apis';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');

	let { saveHandler }: { saveHandler: () => void } = $props();

	let downloading = $state(false);
	let uploading = $state(false);

	let pipelineFiles = $state<FileList | null>(null);

	let PIPELINES_LIST = $state<any[] | null>(null);
	let selectedPipelinesUrlIdx = $state('');

	let pipelines = $state<any[] | null>(null);

	let valves = $state<any>(null);
	let valves_spec = $state<any>(null);
	let selectedPipelineIdx = $state<number | null>(null);

	let pipelineDownloadUrl = $state('');

	const updateHandler = async () => {
		const pipeline = selectedPipelineIdx !== null ? pipelines?.[selectedPipelineIdx] : null;

		if (pipeline && (pipeline?.valves ?? false)) {
			for (const property in valves_spec.properties) {
				if (valves_spec.properties[property]?.type === 'array') {
					valves[property] = valves[property].split(',').map((v: string) => v.trim());
				}
			}

			const res = await updatePipelineValves(pipeline.id, valves, selectedPipelinesUrlIdx).catch(
				(error) => {
					toast.error(`${error}`);
				}
			);

			if (res) {
				toast.success($i18n.t('Valves updated successfully'));
				setPipelines();
				models.set(await getModels());
				saveHandler();
			}
		} else {
			toast.error($i18n.t('No valves to update'));
		}
	};

	const getValves = async (idx: number) => {
		valves = null;
		valves_spec = null;

		valves_spec = await getPipelineValvesSpec(pipelines?.[idx].id, selectedPipelinesUrlIdx);
		valves = await getPipelineValves(pipelines?.[idx].id, selectedPipelinesUrlIdx);

		for (const property in valves_spec.properties) {
			if (valves_spec.properties[property]?.type === 'array') {
				valves[property] = valves[property].join(',');
			}
		}
	};

	const setPipelines = async () => {
		pipelines = null;
		valves = null;
		valves_spec = null;

		if ((PIPELINES_LIST ?? []).length > 0) {
			pipelines = await getPipelines(selectedPipelinesUrlIdx);

			if ((pipelines ?? []).length > 0) {
				selectedPipelineIdx = 0;
				await getValves(selectedPipelineIdx);
			}
		} else {
			pipelines = [];
		}
	};

	const addPipelineHandler = async () => {
		downloading = true;
		const res = await downloadPipeline(pipelineDownloadUrl, selectedPipelinesUrlIdx).catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);

		if (res) {
			toast.success($i18n.t('Pipeline downloaded successfully'));
			setPipelines();
			models.set(await getModels());
		}

		downloading = false;
	};

	const uploadPipelineHandler = async () => {
		uploading = true;

		if (pipelineFiles && pipelineFiles.length !== 0) {
			const file = pipelineFiles[0];

			const res = await uploadPipeline(file, selectedPipelinesUrlIdx).catch(() => {
				toast.error('Something went wrong :/');
				return null;
			});

			if (res) {
				toast.success($i18n.t('Pipeline downloaded successfully'));
				setPipelines();
				models.set(await getModels());
			}
		} else {
			toast.error($i18n.t('No file selected'));
		}

		pipelineFiles = null;
		const pipelineUploadInputElement = document.getElementById(
			'pipelines-upload-input'
		) as HTMLInputElement | null;

		if (pipelineUploadInputElement) {
			pipelineUploadInputElement.value = '';
		}

		uploading = false;
	};

	const deletePipelineHandler = async () => {
		const res = await deletePipeline(
			selectedPipelineIdx !== null ? pipelines?.[selectedPipelineIdx].id : null,
			selectedPipelinesUrlIdx
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Pipeline deleted successfully'));
			setPipelines();
			models.set(await getModels());
		}
	};

	onMount(async () => {
		PIPELINES_LIST = await getPipelinesList();

		if ((PIPELINES_LIST ?? []).length > 0) {
			selectedPipelinesUrlIdx = PIPELINES_LIST![0]['idx'].toString();
		}

		await setPipelines();
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	onsubmit={(e) => {
		e.preventDefault();
		updateHandler();
	}}
>
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		{#if PIPELINES_LIST !== null}
			<div class="flex w-full justify-between mb-2">
				<div class=" self-center text-sm font-semibold">
					{$i18n.t('Manage Pipelines')}
				</div>
			</div>

			{#if PIPELINES_LIST.length > 0}
				<div class="space-y-1">
					<div class="flex gap-2">
						<div class="flex-1">
							<select
								class="w-full rounded-xs py-2 px-4 text-sm text-content bg-surface-form hover:bg-surface-hover outline-hidden"
								bind:value={selectedPipelinesUrlIdx}
								placeholder={$i18n.t('Select a pipeline url')}
								onchange={async () => {
									await tick();
									await setPipelines();
								}}
							>
								<option value="" selected disabled>{$i18n.t('Select a pipeline url')}</option>

								{#each PIPELINES_LIST as pipelines (pipelines.idx)}
									<option value={pipelines.idx.toString()}>{pipelines.url}</option>
								{/each}
							</select>
						</div>
					</div>
				</div>

				<div class=" my-2">
					<div class=" mb-2 text-sm font-medium">
						{$i18n.t('Upload Pipeline')}
					</div>
					<div class="flex w-full">
						<div class="flex-1 mr-2">
							<input
								id="pipelines-upload-input"
								bind:files={pipelineFiles}
								type="file"
								accept=".py"
								hidden
							/>

							<button
								class="w-full text-sm font-medium py-2 bg-transparent hover:bg-surface-hover border border-dashed border-line text-center rounded-xl"
								type="button"
								onclick={() => {
									document.getElementById('pipelines-upload-input')?.click();
								}}
							>
								{#if pipelineFiles}
									{pipelineFiles.length > 0 ? `${pipelineFiles.length}` : ''} pipeline(s) selected.
								{:else}
									{$i18n.t('Click here to select a py file.')}
								{/if}
							</button>
						</div>
						<button
							class="px-2.5 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg"
							aria-label={$i18n.t('Upload')}
							onclick={() => {
								uploadPipelineHandler();
							}}
							disabled={uploading}
							type="button"
						>
							{#if uploading}
								<Spinner className="size-4" />
							{:else}
								<span class="i-lucide-upload size-4 block"></span>
							{/if}
						</button>
					</div>
				</div>

				<div class=" my-2">
					<div class=" mb-2 text-sm font-medium">
						{$i18n.t('Install from Github URL')}
					</div>
					<div class="flex w-full">
						<div class="flex-1 mr-2">
							<input
								class="w-full text-sm px-2 py-1 text-content bg-surface-form rounded-xs outline-hidden"
								placeholder={$i18n.t('Enter Github Raw URL')}
								bind:value={pipelineDownloadUrl}
							/>
						</div>
						<button
							class="px-2.5 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg"
							aria-label={$i18n.t('Download')}
							onclick={() => {
								addPipelineHandler();
							}}
							disabled={downloading}
							type="button"
						>
							{#if downloading}
								<Spinner className="size-4" />
							{:else}
								<span class="i-lucide-download size-4 block"></span>
							{/if}
						</button>
					</div>

					<div class="mt-2 text-xs text-content-muted">
						<span class=" font-semibold text-content">Warning:</span> Pipelines are a plugin system
						with arbitrary code execution —
						<span class=" font-medium text-content-muted"
							>don't fetch random pipelines from sources you don't trust.</span
						>
					</div>
				</div>

				<hr class="border-line my-3 w-full" />

				{#if pipelines !== null}
					{#if pipelines.length > 0}
						<div class="flex w-full justify-between mb-2">
							<div class=" self-center text-sm font-semibold">
								{$i18n.t('Pipelines Valves')}
							</div>
						</div>
						<div class="space-y-1">
							{#if pipelines.length > 0}
								<div class="flex gap-2">
									<div class="flex-1">
										<select
											class="w-full rounded-xs py-2 px-4 text-sm text-content bg-surface-form hover:bg-surface-hover outline-hidden"
											bind:value={selectedPipelineIdx}
											placeholder={$i18n.t('Select a pipeline')}
											onchange={async () => {
												await tick();
												if (selectedPipelineIdx !== null) {
													await getValves(selectedPipelineIdx);
												}
											}}
										>
											{#each pipelines as pipeline, idx (pipeline.id)}
												<option value={idx}>{pipeline.name} ({pipeline.type ?? 'pipe'})</option>
											{/each}
										</select>
									</div>

									<button
										class="px-2.5 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg"
										aria-label={$i18n.t('Delete')}
										onclick={() => {
											deletePipelineHandler();
										}}
										type="button"
									>
										<span class="i-lucide-trash-2 size-4 block"></span>
									</button>
								</div>
							{/if}

							<div class="space-y-1">
								{#if selectedPipelineIdx !== null && pipelines[selectedPipelineIdx]?.valves}
									{#if valves}
										{#each Object.keys(valves_spec.properties) as property (property)}
											<div class=" py-0.5 w-full justify-between">
												<div class="flex w-full justify-between">
													<div class=" self-center text-xs font-medium">
														{valves_spec.properties[property].title}
													</div>

													<button
														class="p-1 px-3 text-xs flex rounded-sm"
														type="button"
														onclick={() => {
															valves[property] = (valves[property] ?? null) === null ? '' : null;
														}}
													>
														{#if (valves[property] ?? null) === null}
															<span class="ml-2 self-center"> {$i18n.t('None')} </span>
														{:else}
															<span class="ml-2 self-center"> {$i18n.t('Custom')} </span>
														{/if}
													</button>
												</div>

												{#if (valves[property] ?? null) !== null}
													<div class="flex mt-0.5 mb-1.5 space-x-2">
														<div class=" flex-1">
															{#if valves_spec.properties[property]?.enum ?? null}
																<select
																	class="w-full rounded-xs py-2 px-4 text-sm text-content bg-surface-form hover:bg-surface-hover outline-hidden"
																	bind:value={valves[property]}
																>
																	{#each valves_spec.properties[property].enum as option}
																		<option value={option} selected={option === valves[property]}>
																			{option}
																		</option>
																	{/each}
																</select>
															{:else if (valves_spec.properties[property]?.type ?? null) === 'boolean'}
																<div class="flex justify-between items-center">
																	<div class="text-xs text-content-muted">
																		{valves[property] ? 'Enabled' : 'Disabled'}
																	</div>

																	<div class=" pr-2">
																		<Switch bind:state={valves[property]} />
																	</div>
																</div>
															{:else}
																<input
																	class="w-full text-sm px-2 py-1 text-content bg-surface-form rounded-xs outline-hidden"
																	type="text"
																	placeholder={valves_spec.properties[property].title}
																	bind:value={valves[property]}
																	autocomplete="off"
																	required
																/>
															{/if}
														</div>
													</div>
												{/if}
											</div>
										{/each}
									{:else}
										<Spinner className="size-5" />
									{/if}
								{:else}
									<div>No valves</div>
								{/if}
							</div>
						</div>
					{:else if pipelines.length === 0}
						<div>Pipelines Not Detected</div>
					{/if}
				{:else}
					<div class="flex justify-center">
						<div class="my-auto">
							<Spinner className="size-4" />
						</div>
					</div>
				{/if}
			{:else}
				<div>{$i18n.t('Pipelines Not Detected')}</div>
			{/if}
		{:else}
			<div class="flex justify-center h-full">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}
	</div>

	{#if PIPELINES_LIST !== null && PIPELINES_LIST.length > 0}
		<div class="flex justify-end pt-3 text-sm font-medium">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
				type="submit"
			>
				{$i18n.t('Save')}
			</button>
		</div>
	{/if}
</form>
