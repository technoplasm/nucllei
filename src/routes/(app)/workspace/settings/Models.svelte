<script lang="ts">
	import { render } from '$lib/utils/markdown-it';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { models as _models, user } from '$lib/stores';
	import {
		createNewModel,
		getBaseModels,
		toggleModelById,
		updateModelById
	} from '$lib/apis/models';

	import { getModels } from '$lib/apis';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import ModelEditor from '../models/ModelEditor.svelte';
	import { toast } from 'svelte-sonner';
	import ManageModelsModal from './Models/ManageModelsModal.svelte';
	import ModelMenu from './Models/ModelMenu.svelte';

	let shiftKey = $state(false);

	let importFiles = $state<FileList | null>(null);
	let modelsImportInputElement = $state<HTMLInputElement | null>(null);

	let models = $state<any[] | null>(null);

	let workspaceModels = $state<any[] | null>(null);

	let selectedModelId = $state<string | null>(null);

	let showManageModal = $state(false);

	let searchValue = $state('');

	const filteredModels = $derived(
		(models ?? [])
			.filter((m) => searchValue === '' || m.name.toLowerCase().includes(searchValue.toLowerCase()))
			.sort((a, b) => a.name.localeCompare(b.name))
	);

	const downloadModels = async (models: any[]) => {
		let blob = new Blob([JSON.stringify(models)], {
			type: 'application/json'
		});
		saveAs(blob, `models-export-${Date.now()}.json`);
	};

	const init = async () => {
		workspaceModels = await getBaseModels();
		const baseModels = await getModels(true);

		models = baseModels.map((m: any) => {
			const workspaceModel = (workspaceModels ?? []).find((wm: any) => wm.id === m.id);

			if (workspaceModel) {
				return {
					...m,
					...workspaceModel
				};
			} else {
				return {
					...m,
					id: m.id,
					name: m.name,

					is_active: true
				};
			}
		});
	};

	const upsertModelHandler = async (model: any) => {
		model.base_model_id = null;

		if ((workspaceModels ?? []).find((m: any) => m.id === model.id)) {
			const res = await updateModelById(model.id, model).catch(() => {
				return null;
			});

			if (res) {
				toast.success($i18n.t('Model updated successfully'));
			}
		} else {
			const res = await createNewModel({
				meta: {},
				id: model.id,
				name: model.name,
				base_model_id: null,
				params: {},
				access_control: {},
				...model
			}).catch(() => {
				return null;
			});

			if (res) {
				toast.success($i18n.t('Model updated successfully'));
			}
		}

		_models.set(await getModels());
		await init();
	};

	const toggleModelHandler = async (model: any) => {
		if (!Object.keys(model).includes('base_model_id')) {
			await createNewModel({
				id: model.id,
				name: model.name,
				base_model_id: null,
				meta: {},
				params: {},
				access_control: {},
				is_active: model.is_active
			}).catch(() => {
				return null;
			});
		} else {
			await toggleModelById(model.id);
		}

		_models.set(await getModels());
	};

	const hideModelHandler = async (model: any) => {
		model.meta = {
			...model.meta,
			hidden: !(model?.meta?.hidden ?? false)
		};

		toast.success(
			model.meta.hidden
				? $i18n.t(`Model {{name}} is now hidden`, {
						name: model.id
					})
				: $i18n.t(`Model {{name}} is now visible`, {
						name: model.id
					})
		);

		upsertModelHandler(model);
	};

	const exportModelHandler = async (model: any) => {
		let blob = new Blob([JSON.stringify([model])], {
			type: 'application/json'
		});
		saveAs(blob, `${model.id}-${Date.now()}.json`);
	};

	onMount(() => {
		// async にすると cleanup 関数が Promise に包まれて呼ばれない(旧コードのバグ)。
		// init はリスナー登録と独立なので待たずに走らせる。
		init();

		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === 'Shift') {
				shiftKey = true;
			}
		};

		const onKeyUp = (event: KeyboardEvent) => {
			if (event.key === 'Shift') {
				shiftKey = false;
			}
		};

		const onBlur = () => {
			shiftKey = false;
		};

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);
		// 旧コードは 'blur-sm'(tailwind 一括リネームが JS のイベント名を巻き込んだもの)で
		// 一度 Shift 押下のままウィンドウを離れると解除されなかった
		window.addEventListener('blur', onBlur);

		return () => {
			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('keyup', onKeyUp);
			window.removeEventListener('blur', onBlur);
		};
	});
</script>

<!-- llens(#70): 表示順・既定モデルの ConfigureModelsModal は workspace/models へ移設 -->
<ManageModelsModal bind:show={showManageModal} />

{#if models !== null}
	{#if selectedModelId === null}
		<div class="flex flex-col gap-1 mt-1.5 mb-2">
			<div class="flex items-center text-xl font-medium px-0.5">
				{$i18n.t('Models')}
			</div>

			<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
				<div class="flex items-center px-2 bg-surface-raised rounded-xs">
					<span class="i-lucide-search size-3.5 block shrink-0"></span>
					<input
						class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
						bind:value={searchValue}
						placeholder={$i18n.t('Search Models')}
					/>
				</div>

				<div class="ml-auto flex items-center gap-0.5">
					<Tooltip content={$i18n.t('Manage Models')}>
						<button
							class="p-1.5 rounded-lg text-content-muted hover:bg-surface-hover"
							type="button"
							aria-label={$i18n.t('Manage Models')}
							onclick={() => {
								showManageModal = true;
							}}
						>
							<span class="i-lucide-download size-4 block"></span>
						</button>
					</Tooltip>
				</div>
			</div>
		</div>

		<div class=" my-2 mb-5" id="model-list">
			{#if models.length > 0}
				{#each filteredModels as model (model.id)}
					<div
						class=" flex space-x-4 cursor-pointer w-full px-3 py-2 [&:hover:not(:has(.card-action:hover))]:bg-surface-hover rounded-lg {model
							?.meta?.hidden
							? 'opacity-50 dark:opacity-50'
							: ''}"
						id="model-item-{model.id}"
					>
						<button
							class=" flex flex-1 text-left space-x-3.5 cursor-pointer w-full"
							type="button"
							onclick={() => {
								selectedModelId = model.id;
							}}
						>
							<div class=" self-center w-8">
								<div
									class=" rounded-full object-cover {(model?.is_active ?? true)
										? ''
										: 'opacity-50 dark:opacity-50'} "
								>
									<img
										src={model?.meta?.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
										alt="modelfile profile"
										class=" rounded-full w-full h-auto object-cover"
									/>
								</div>
							</div>

							<div
								class=" flex-1 self-center {(model?.is_active ?? true)
									? ''
									: 'text-content-subtle'}"
							>
								<Tooltip
									content={render(
										!!model?.meta?.description
											? model?.meta?.description
											: model?.ollama?.digest
												? `${model?.ollama?.digest} **(${model?.ollama?.modified_at})**`
												: model.id
									)}
									className=" w-fit"
									placement="top-start"
								>
									<div class="  font-semibold line-clamp-1">{model.name}</div>
								</Tooltip>
								<div class=" text-xs overflow-hidden text-ellipsis line-clamp-1 text-content-muted">
									<span class=" line-clamp-1">
										{!!model?.meta?.description
											? model?.meta?.description
											: model?.ollama?.digest
												? `${model.id} (${model?.ollama?.digest})`
												: model.id}
									</span>
								</div>
							</div>
						</button>
						<div class="flex flex-row gap-0.5 items-center self-center">
							{#if shiftKey}
								<Tooltip content={model?.meta?.hidden ? $i18n.t('Show') : $i18n.t('Hide')}>
									<button
										class="card-action self-center w-fit text-sm px-2 py-2 text-content hover:bg-surface-hover rounded-xl"
										type="button"
										aria-label={model?.meta?.hidden ? $i18n.t('Show') : $i18n.t('Hide')}
										onclick={() => {
											hideModelHandler(model);
										}}
									>
										{#if model?.meta?.hidden}
											<span class="i-lucide-eye-off size-4 block"></span>
										{:else}
											<span class="i-lucide-eye size-4 block"></span>
										{/if}
									</button>
								</Tooltip>
							{:else}
								<button
									class="card-action self-center w-fit text-sm px-2 py-2 text-content hover:bg-surface-hover rounded-xl"
									type="button"
									aria-label={$i18n.t('Edit')}
									onclick={() => {
										selectedModelId = model.id;
									}}
								>
									<span class="i-lucide-pencil size-4 block"></span>
								</button>

								<ModelMenu
									{model}
									exportHandler={() => {
										exportModelHandler(model);
									}}
									hideHandler={() => {
										hideModelHandler(model);
									}}
									onClose={() => {}}
								>
									<button
										class="card-action self-center w-fit text-sm p-1.5 text-content hover:bg-surface-hover rounded-xl"
										type="button"
										aria-label={$i18n.t('More')}
									>
										<span class="i-lucide-ellipsis size-5 block"></span>
									</button>
								</ModelMenu>

								<div class="ml-1">
									<Tooltip
										content={(model?.is_active ?? true) ? $i18n.t('Enabled') : $i18n.t('Disabled')}
									>
										<Switch
											bind:state={model.is_active}
											on:change={async () => {
												toggleModelHandler(model);
											}}
										/>
									</Tooltip>
								</div>
							{/if}
						</div>
					</div>
				{/each}
			{:else}
				<div class="flex flex-col items-center justify-center w-full h-20">
					<div class="text-content-subtle text-xs">
						{$i18n.t('No models found')}
					</div>
				</div>
			{/if}
		</div>

		{#if $user?.role === 'admin'}
			<div class=" flex justify-end w-full mb-3">
				<div class="flex space-x-1">
					<input
						id="models-import-input"
						bind:this={modelsImportInputElement}
						bind:files={importFiles}
						type="file"
						accept=".json"
						hidden
						onchange={() => {
							if (!importFiles || importFiles.length === 0) {
								return;
							}

							let reader = new FileReader();
							reader.onload = async (event) => {
								let savedModels = JSON.parse(event.target?.result as string);

								for (const model of savedModels) {
									if (Object.keys(model).includes('base_model_id')) {
										if (model.base_model_id === null) {
											upsertModelHandler(model);
										}
									} else {
										if (model?.info ?? false) {
											if (model.info.base_model_id === null) {
												upsertModelHandler(model.info);
											}
										}
									}
								}

								await _models.set(await getModels());
								init();
							};

							reader.readAsText(importFiles[0]);
						}}
					/>

					<button
						class="flex text-xs items-center space-x-1 px-3 py-1.5 rounded-xl bg-secondary hover:bg-secondary-hover text-secondary-content"
						onclick={() => {
							modelsImportInputElement?.click();
						}}
					>
						<div class=" self-center mr-2 font-medium line-clamp-1">
							{$i18n.t('Import Presets')}
						</div>

						<span class="i-lucide-file-up size-3.5 block self-center"></span>
					</button>

					<button
						class="flex text-xs items-center space-x-1 px-3 py-1.5 rounded-xl bg-secondary hover:bg-secondary-hover text-secondary-content"
						onclick={async () => {
							downloadModels(models ?? []);
						}}
					>
						<div class=" self-center mr-2 font-medium line-clamp-1">
							{$i18n.t('Export Presets')}
						</div>

						<span class="i-lucide-file-down size-3.5 block self-center"></span>
					</button>
				</div>
			</div>
		{/if}
	{:else}
		<ModelEditor
			edit
			model={models.find((m) => m.id === selectedModelId)}
			preset={false}
			onSubmit={(model: any) => {
				upsertModelHandler(model);
				selectedModelId = null;
			}}
			onBack={() => {
				selectedModelId = null;
			}}
		/>
	{/if}
{:else}
	<div class=" h-full w-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
