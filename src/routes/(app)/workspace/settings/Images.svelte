<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { onMount, getContext } from 'svelte';
	import { config as backendConfig, user } from '$lib/stores';

	import { getBackendConfig } from '$lib/apis';
	import {
		getImageGenerationModels,
		getImageGenerationConfig,
		updateImageGenerationConfig,
		getConfig,
		updateConfig,
		verifyConfigUrl
	} from '$lib/apis/images';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let { onSave = () => {} }: { onSave?: () => void } = $props();

	let loading = $state(false);

	let config = $state<any>(null);
	let imageGenerationConfig = $state<any>(null);

	let models = $state<any[] | null>(null);

	const samplers = [
		'DPM++ 2M',
		'DPM++ SDE',
		'DPM++ 2M SDE',
		'DPM++ 2M SDE Heun',
		'DPM++ 2S a',
		'DPM++ 3M SDE',
		'Euler a',
		'Euler',
		'LMS',
		'Heun',
		'DPM2',
		'DPM2 a',
		'DPM fast',
		'DPM adaptive',
		'Restart',
		'DDIM',
		'DDIM CFG++',
		'PLMS',
		'UniPC'
	];

	const schedulers = [
		'Automatic',
		'Uniform',
		'Karras',
		'Exponential',
		'Polyexponential',
		'SGM Uniform',
		'KL Optimal',
		'Align Your Steps',
		'Simple',
		'Normal',
		'DDIM',
		'Beta'
	];

	let requiredWorkflowNodes = $state([
		{
			type: 'prompt',
			key: 'text',
			node_ids: ''
		},
		{
			type: 'model',
			key: 'ckpt_name',
			node_ids: ''
		},
		{
			type: 'width',
			key: 'width',
			node_ids: ''
		},
		{
			type: 'height',
			key: 'height',
			node_ids: ''
		},
		{
			type: 'steps',
			key: 'steps',
			node_ids: ''
		},
		{
			type: 'seed',
			key: 'seed',
			node_ids: ''
		}
	]);

	const getModels = async () => {
		models = await getImageGenerationModels().catch((error) => {
			toast.error(`${error}`);
			return null;
		});
	};

	const updateConfigHandler = async () => {
		const res = await updateConfig(config).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			config = res;
		}

		if (config.enabled) {
			backendConfig.set(await getBackendConfig());
			getModels();
		}
	};

	const validateJSON = (json: string) => {
		try {
			const obj = JSON.parse(json);

			if (obj && typeof obj === 'object') {
				return true;
			}
		} catch {
			// 不正 JSON は false を返すだけ
		}
		return false;
	};

	const saveHandler = async () => {
		loading = true;

		if (config?.comfyui?.COMFYUI_WORKFLOW) {
			if (!validateJSON(config.comfyui.COMFYUI_WORKFLOW)) {
				toast.error('Invalid JSON format for ComfyUI Workflow.');
				loading = false;
				return;
			}
		}

		if (config?.comfyui?.COMFYUI_WORKFLOW) {
			config.comfyui.COMFYUI_WORKFLOW_NODES = requiredWorkflowNodes.map((node) => {
				return {
					type: node.type,
					key: node.key,
					node_ids:
						node.node_ids.trim() === '' ? [] : node.node_ids.split(',').map((id) => id.trim())
				};
			});
		}

		await updateConfig(config).catch((error) => {
			toast.error(`${error}`);
			loading = false;
			return null;
		});

		await updateImageGenerationConfig(imageGenerationConfig).catch((error) => {
			toast.error(`${error}`);
			loading = false;
			return null;
		});

		getModels();
		onSave();
		loading = false;
	};

	onMount(async () => {
		if ($user?.role === 'admin') {
			const res = await getConfig().catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (res) {
				config = res;
			}

			if (config.enabled) {
				getModels();
			}

			if (config.comfyui.COMFYUI_WORKFLOW) {
				try {
					config.comfyui.COMFYUI_WORKFLOW = JSON.stringify(
						JSON.parse(config.comfyui.COMFYUI_WORKFLOW),
						null,
						2
					);
				} catch {
					// 整形失敗時は保存値をそのまま見せる
				}
			}

			requiredWorkflowNodes = requiredWorkflowNodes.map((node) => {
				const n =
					config.comfyui.COMFYUI_WORKFLOW_NODES.find((n: any) => n.type === node.type) ?? node;

				return {
					type: n.type,
					key: n.key,
					node_ids: typeof n.node_ids === 'string' ? n.node_ids : n.node_ids.join(',')
				};
			});

			const imageConfigRes = await getImageGenerationConfig().catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (imageConfigRes) {
				imageGenerationConfig = imageConfigRes;
			}
		}
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	onsubmit={(e) => {
		e.preventDefault();
		saveHandler();
	}}
>
	<div class=" space-y-3 overflow-y-scroll scrollbar-hidden pr-2">
		{#if config && imageGenerationConfig}
			<div>
				<div class=" mb-1 text-sm font-medium">{$i18n.t('Image Settings')}</div>

				<div>
					<div class=" py-1 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">
							{$i18n.t('Image Generation (Experimental)')}
						</div>

						<div class="px-1">
							<Switch
								bind:state={config.enabled}
								on:change={(e) => {
									const enabled = e.detail;

									if (enabled) {
										if (
											config.engine === 'automatic1111' &&
											config.automatic1111.AUTOMATIC1111_BASE_URL === ''
										) {
											toast.error($i18n.t('AUTOMATIC1111 Base URL is required.'));
											config.enabled = false;
										} else if (
											config.engine === 'comfyui' &&
											config.comfyui.COMFYUI_BASE_URL === ''
										) {
											toast.error($i18n.t('ComfyUI Base URL is required.'));
											config.enabled = false;
										} else if (config.engine === 'openai' && config.openai.OPENAI_API_KEY === '') {
											toast.error($i18n.t('OpenAI API Key is required.'));
											config.enabled = false;
										} else if (config.engine === 'gemini' && config.gemini.GEMINI_API_KEY === '') {
											toast.error($i18n.t('Gemini API Key is required.'));
											config.enabled = false;
										}
									}

									updateConfigHandler();
								}}
							/>
						</div>
					</div>
				</div>

				{#if config.enabled}
					<div class=" py-1 flex w-full justify-between">
						<div class=" self-center text-xs font-medium">{$i18n.t('Image Prompt Generation')}</div>
						<div class="px-1">
							<Switch bind:state={config.prompt_generation} />
						</div>
					</div>
				{/if}

				<div class=" py-1 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Image Generation Engine')}</div>
					<div class="flex items-center relative">
						<select
							class="bg-surface-raised hover:bg-surface-hover w-fit cursor-pointer px-2 py-0.5 pr-6 rounded-xs text-xs outline-hidden text-right"
							bind:value={config.engine}
							placeholder={$i18n.t('Select Engine')}
							onchange={async () => {
								updateConfigHandler();
							}}
						>
							<option value="openai">{$i18n.t('Default (Open AI)')}</option>
							<option value="comfyui">{$i18n.t('ComfyUI')}</option>
							<option value="automatic1111">{$i18n.t('Automatic1111')}</option>
							<option value="gemini">{$i18n.t('Gemini')}</option>
						</select>
					</div>
				</div>
			</div>
			<hr class=" border-line" />

			<div class="flex flex-col gap-2">
				{#if (config?.engine ?? 'automatic1111') === 'automatic1111'}
					<div>
						<div class=" mb-2 text-sm font-medium">{$i18n.t('AUTOMATIC1111 Base URL')}</div>
						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<input
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									placeholder={$i18n.t('Enter URL (e.g. http://127.0.0.1:7860/)')}
									bind:value={config.automatic1111.AUTOMATIC1111_BASE_URL}
								/>
							</div>
							<button
								class="px-2.5 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg"
								type="button"
								aria-label={$i18n.t('Verify Connection')}
								onclick={async () => {
									await updateConfigHandler();
									const res = await verifyConfigUrl().catch((error) => {
										toast.error(`${error}`);
										return null;
									});

									if (res) {
										toast.success($i18n.t('Server connection verified'));
									}
								}}
							>
								<span class="i-lucide-refresh-cw size-4 block"></span>
							</button>
						</div>

						<div class="mt-2 text-xs text-content-subtle">
							{$i18n.t('Include `--api` flag when running stable-diffusion-webui')}
							<a
								class=" text-content-subtle font-medium"
								href="https://github.com/AUTOMATIC1111/stable-diffusion-webui/discussions/3734"
								target="_blank"
							>
								{$i18n.t('(e.g. `sh webui.sh --api`)')}
							</a>
						</div>
					</div>

					<div>
						<div class=" mb-2 text-sm font-medium">
							{$i18n.t('AUTOMATIC1111 Api Auth String')}
						</div>
						<SensitiveInput
							outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
							inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
							placeholder={$i18n.t('Enter api auth string (e.g. username:password)')}
							bind:value={config.automatic1111.AUTOMATIC1111_API_AUTH}
							required={false}
						/>

						<div class="mt-2 text-xs text-content-subtle">
							{$i18n.t('Include `--api-auth` flag when running stable-diffusion-webui')}
							<a
								class=" text-content-subtle font-medium"
								href="https://github.com/AUTOMATIC1111/stable-diffusion-webui/discussions/13993"
								target="_blank"
							>
								{$i18n
									.t('(e.g. `sh webui.sh --api --api-auth username_password`)')
									.replace('_', ':')}
							</a>
						</div>
					</div>

					<!---Sampler-->
					<div>
						<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Sampler')}</div>
						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<Tooltip content={$i18n.t('Enter Sampler (e.g. Euler a)')} placement="top-start">
									<input
										list="sampler-list"
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										placeholder={$i18n.t('Enter Sampler (e.g. Euler a)')}
										bind:value={config.automatic1111.AUTOMATIC1111_SAMPLER}
									/>

									<datalist id="sampler-list">
										{#each samplers ?? [] as sampler}
											<option value={sampler}>{sampler}</option>
										{/each}
									</datalist>
								</Tooltip>
							</div>
						</div>
					</div>
					<!---Scheduler-->
					<div>
						<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Scheduler')}</div>
						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<Tooltip content={$i18n.t('Enter Scheduler (e.g. Karras)')} placement="top-start">
									<input
										list="scheduler-list"
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										placeholder={$i18n.t('Enter Scheduler (e.g. Karras)')}
										bind:value={config.automatic1111.AUTOMATIC1111_SCHEDULER}
									/>

									<datalist id="scheduler-list">
										{#each schedulers ?? [] as scheduler}
											<option value={scheduler}>{scheduler}</option>
										{/each}
									</datalist>
								</Tooltip>
							</div>
						</div>
					</div>
					<!---CFG scale-->
					<div>
						<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set CFG Scale')}</div>
						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<Tooltip content={$i18n.t('Enter CFG Scale (e.g. 7.0)')} placement="top-start">
									<input
										class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
										placeholder={$i18n.t('Enter CFG Scale (e.g. 7.0)')}
										bind:value={config.automatic1111.AUTOMATIC1111_CFG_SCALE}
									/>
								</Tooltip>
							</div>
						</div>
					</div>
				{:else if config?.engine === 'comfyui'}
					<div class="">
						<div class=" mb-2 text-sm font-medium">{$i18n.t('ComfyUI Base URL')}</div>
						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<input
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									placeholder={$i18n.t('Enter URL (e.g. http://127.0.0.1:7860/)')}
									bind:value={config.comfyui.COMFYUI_BASE_URL}
								/>
							</div>
							<button
								class="px-2.5 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg"
								type="button"
								aria-label={$i18n.t('Verify Connection')}
								onclick={async () => {
									await updateConfigHandler();
									const res = await verifyConfigUrl().catch((error) => {
										toast.error(`${error}`);
										return null;
									});

									if (res) {
										toast.success($i18n.t('Server connection verified'));
									}
								}}
							>
								<span class="i-lucide-refresh-cw size-4 block"></span>
							</button>
						</div>
					</div>

					<div class="">
						<div class=" mb-2 text-sm font-medium">{$i18n.t('ComfyUI API Key')}</div>
						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<SensitiveInput
									outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
									inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
									placeholder={$i18n.t('sk-1234')}
									bind:value={config.comfyui.COMFYUI_API_KEY}
									required={false}
								/>
							</div>
						</div>
					</div>

					<div class="">
						<div class=" mb-2 text-sm font-medium">{$i18n.t('ComfyUI Workflow')}</div>

						{#if config.comfyui.COMFYUI_WORKFLOW}
							<textarea
								class="w-full rounded-xs mb-1 py-2 px-4 text-xs bg-surface-raised text-content outline-hidden disabled:text-content-subtle resize-none"
								rows="10"
								bind:value={config.comfyui.COMFYUI_WORKFLOW}
								required
							></textarea>
						{/if}

						<div class="flex w-full">
							<div class="flex-1">
								<input
									id="upload-comfyui-workflow-input"
									hidden
									type="file"
									accept=".json"
									onchange={(e) => {
										const inputElement = e.currentTarget;
										const file = inputElement.files?.[0];
										if (!file) {
											return;
										}
										const reader = new FileReader();

										reader.onload = (event) => {
											config.comfyui.COMFYUI_WORKFLOW = event.target?.result as string;
											inputElement.value = '';
										};

										reader.readAsText(file);
									}}
								/>

								<button
									class="w-full text-sm font-medium py-2 bg-transparent hover:bg-surface-hover border border-dashed border-line text-center rounded-xl"
									type="button"
									onclick={() => {
										document.getElementById('upload-comfyui-workflow-input')?.click();
									}}
								>
									{$i18n.t('Click here to upload a workflow.json file.')}
								</button>
							</div>
						</div>

						<div class="mt-2 text-xs text-content-subtle">
							{$i18n.t('Make sure to export a workflow.json file as API format from ComfyUI.')}
						</div>
					</div>

					{#if config.comfyui.COMFYUI_WORKFLOW}
						<div class="">
							<div class=" mb-2 text-sm font-medium">{$i18n.t('ComfyUI Workflow Nodes')}</div>

							<div class="text-xs flex flex-col gap-1.5">
								{#each requiredWorkflowNodes as node}
									<div class="flex w-full items-center border border-line rounded-lg">
										<div class="shrink-0">
											<div
												class=" capitalize line-clamp-1 font-medium px-3 py-1 w-20 text-center rounded-l-lg bg-green-500/10 text-green-700 dark:text-green-200"
											>
												{node.type}{node.type === 'prompt' ? '*' : ''}
											</div>
										</div>
										<div class="">
											<Tooltip content="Input Key (e.g. text, unet_name, steps)">
												<input
													class="py-1 px-3 w-24 text-xs text-center bg-transparent outline-hidden border-r border-line"
													placeholder="Key"
													bind:value={node.key}
													required
												/>
											</Tooltip>
										</div>

										<div class="w-full">
											<Tooltip
												content="Comma separated Node Ids (e.g. 1 or 1,2)"
												placement="top-start"
											>
												<input
													class="w-full py-1 px-4 rounded-r-lg text-xs bg-transparent outline-hidden"
													placeholder="Node Ids"
													bind:value={node.node_ids}
												/>
											</Tooltip>
										</div>
									</div>
								{/each}
							</div>

							<div class="mt-2 text-xs text-right text-content-subtle">
								{$i18n.t('*Prompt node ID(s) are required for image generation')}
							</div>
						</div>
					{/if}
				{:else if config?.engine === 'openai'}
					<div>
						<div class=" mb-1.5 text-sm font-medium">{$i18n.t('OpenAI API Config')}</div>

						<div class="flex gap-2 mb-1">
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('API Base URL')}
								bind:value={config.openai.OPENAI_API_BASE_URL}
								required
							/>

							<SensitiveInput
								outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
								inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={config.openai.OPENAI_API_KEY}
							/>
						</div>
					</div>
				{:else if config?.engine === 'gemini'}
					<div>
						<div class=" mb-1.5 text-sm font-medium">{$i18n.t('Gemini API Config')}</div>

						<div class="flex gap-2 mb-1">
							<input
								class="flex-1 w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
								placeholder={$i18n.t('API Base URL')}
								bind:value={config.gemini.GEMINI_API_BASE_URL}
								required
							/>

							<SensitiveInput
								outerClassName="flex flex-1 px-2 bg-surface-raised rounded-xs"
								inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
								placeholder={$i18n.t('API Key')}
								bind:value={config.gemini.GEMINI_API_KEY}
							/>
						</div>
					</div>
				{/if}
			</div>

			{#if config?.enabled}
				<hr class=" border-line" />

				<div>
					<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Default Model')}</div>
					<div class="flex w-full">
						<div class="flex-1 mr-2">
							<div class="flex w-full">
								<div class="flex-1">
									<Tooltip content={$i18n.t('Enter Model ID')} placement="top-start">
										<input
											list="model-list"
											class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
											bind:value={imageGenerationConfig.MODEL}
											placeholder="Select a model"
											required
										/>

										<datalist id="model-list">
											{#each models ?? [] as model}
												<option value={model.id}>{model.name}</option>
											{/each}
										</datalist>
									</Tooltip>
								</div>
							</div>
						</div>
					</div>
				</div>

				<div>
					<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Image Size')}</div>
					<div class="flex w-full">
						<div class="flex-1 mr-2">
							<Tooltip content={$i18n.t('Enter Image Size (e.g. 512x512)')} placement="top-start">
								<input
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									placeholder={$i18n.t('Enter Image Size (e.g. 512x512)')}
									bind:value={imageGenerationConfig.IMAGE_SIZE}
									required
								/>
							</Tooltip>
						</div>
					</div>
				</div>

				<div>
					<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Set Steps')}</div>
					<div class="flex w-full">
						<div class="flex-1 mr-2">
							<Tooltip content={$i18n.t('Enter Number of Steps (e.g. 50)')} placement="top-start">
								<input
									class="w-full text-sm px-2 py-1 bg-surface-raised rounded-xs outline-hidden"
									placeholder={$i18n.t('Enter Number of Steps (e.g. 50)')}
									bind:value={imageGenerationConfig.IMAGE_STEPS}
									required
								/>
							</Tooltip>
						</div>
					</div>
				</div>
			{/if}
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full flex flex-row space-x-1 items-center {loading
				? ' cursor-not-allowed'
				: ''}"
			type="submit"
			disabled={loading}
		>
			{$i18n.t('Save')}

			{#if loading}
				<div class="ml-2 self-center">
					<Spinner className="size-4" />
				</div>
			{/if}
		</button>
	</div>
</form>
