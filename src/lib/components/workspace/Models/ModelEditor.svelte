<script lang="ts">
	import { onMount, getContext, tick } from 'svelte';
	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { tools, functions, skills, user } from '$lib/stores';

	import AdvancedParams from '$lib/components/chat/Settings/Advanced/AdvancedParams.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import ToolsSelector from '$lib/components/workspace/Models/ToolsSelector.svelte';
	import SkillsSelector from '$lib/components/workspace/Models/SkillsSelector.svelte';
	import FiltersSelector from '$lib/components/workspace/Models/FiltersSelector.svelte';
	import ActionsSelector from '$lib/components/workspace/Models/ActionsSelector.svelte';
	import Capabilities from '$lib/components/workspace/Models/Capabilities.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import { getModels } from '$lib/apis';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { getFunctions } from '$lib/apis/functions';
	import AccessControl from '../common/AccessControl.svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	// 基本設定 | プロンプト | 高度なパラメータ(Admin > Users の様式)
	let selectedTab = 'general';

	export let onSubmit: Function;
	export let onBack: null | Function = null;

	export let model = null;
	export let edit = false;

	export let preset = true;

	let loading = false;
	let success = false;

	let filesInputElement;
	let inputFiles;

	let loaded = false;

	// ///////////
	// model
	// ///////////

	let id = '';
	let name = '';

	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	let originalId = '';
	let showRenameConfirm = false;

	$: if (!edit) {
		if (name) {
			id = name
				.replace(/\s+/g, '-')
				.replace(/[^a-zA-Z0-9-]/g, '')
				.toLowerCase();
		}
	}

	let info = {
		id: '',
		base_model_id: null,
		name: '',
		meta: {
			profile_image_url: null,
			description: '',
			suggestion_prompts: null,
			tags: []
		},
		params: {
			system: ''
		}
	};

	let params = {
		system: ''
	};
	let capabilities = {
		vision: true,
		usage: undefined,
		citations: true
	};

	let toolIds = [];
	let skillIds = [];
	let filterIds = [];
	let actionIds = [];

	// llens(#42): 機能「スキル」(meta.features.skills、既定 ON)。OFF でこのモデルの
	// manifest(load_skill)と /command 発火が両方止まる。
	let featureSkills = true;

	let accessControl = {};

	// llens: チャットのモデルピッカー(/api/models = $models)は preset のみを返すよう
	// になったため、preset のベースモデル選択には生モデルを含む /api/models/base を使う。
	// admin ゲートのエンドポイントなので admin 前提(workspace.models 既定は False)。
	let baseModels = [];

	// llens(#47): タブ切替で「見えていない別タブの編集」をそのまま保存しないよう、ロード時の
	// 状態へ暗黙リセットする。名前/ID はタブ外の常時ヘッダなので対象外(リセットしない)。
	let stateSnapshot = null;
	const captureSnapshot = () => {
		stateSnapshot = JSON.stringify({
			info,
			params,
			capabilities,
			toolIds,
			skillIds,
			filterIds,
			actionIds,
			featureSkills,
			accessControl
		});
	};
	const switchTab = (tab) => {
		if (tab === selectedTab) return;
		if (stateSnapshot) {
			const s = JSON.parse(stateSnapshot);
			info = s.info;
			params = s.params;
			capabilities = s.capabilities;
			toolIds = s.toolIds;
			skillIds = s.skillIds;
			filterIds = s.filterIds;
			actionIds = s.actionIds;
			featureSkills = s.featureSkills;
			accessControl = s.accessControl;
		}
		selectedTab = tab;
	};

	const addUsage = (base_model_id) => {
		const baseModel = baseModels.find((m) => m.id === base_model_id);

		if (baseModel) {
			if (baseModel.owned_by === 'openai') {
				capabilities.usage = baseModel?.meta?.capabilities?.usage ?? false;
			} else {
				delete capabilities.usage;
			}
			capabilities = capabilities;
		}
	};

	const submitHandler = async () => {
		loading = true;

		info.id = id;
		info.name = name;

		if (id === '') {
			toast.error('Model ID is required.');
		}

		if (name === '') {
			toast.error('Model Name is required.');
		}

		info.access_control = accessControl;
		info.meta.capabilities = capabilities;

		info.meta.description =
			(info.meta.description ?? '').trim() === '' ? null : info.meta.description;

		if (toolIds.length > 0) {
			info.meta.toolIds = toolIds;
		} else {
			if (info.meta.toolIds) {
				delete info.meta.toolIds;
			}
		}

		if (skillIds.length > 0) {
			info.meta.skillIds = skillIds;
		} else {
			if (info.meta.skillIds) {
				delete info.meta.skillIds;
			}
		}

		// 既定(ON)のときはキーを残さない(meta を汚さない)
		if (!featureSkills) {
			info.meta.features = { ...(info.meta.features ?? {}), skills: false };
		} else if (info.meta.features?.skills !== undefined) {
			delete info.meta.features.skills;
			if (Object.keys(info.meta.features).length === 0) {
				delete info.meta.features;
			}
		}

		if (filterIds.length > 0) {
			info.meta.filterIds = filterIds;
		} else {
			if (info.meta.filterIds) {
				delete info.meta.filterIds;
			}
		}

		if (actionIds.length > 0) {
			info.meta.actionIds = actionIds;
		} else {
			if (info.meta.actionIds) {
				delete info.meta.actionIds;
			}
		}

		// llens(#69): per-model Attachment(meta.attachmentIds、#52)は撤去。
		// Attachment はグローバル設定(/configs/attachment の ATTACHMENT_FUNCTION_ID)へ移行。
		if (info.meta.attachmentIds) {
			delete info.meta.attachmentIds;
		}

		// プロンプト候補: title と prompt(content)は両方必須(空文字は不可)。空配列は null 化して meta を汚さない。
		if (Array.isArray(info?.meta?.suggestion_prompts)) {
			const prompts = info.meta.suggestion_prompts;
			if (prompts.some((p) => (p?.title ?? '').trim() === '' || (p?.content ?? '').trim() === '')) {
				toast.error($i18n.t('Prompt suggestions require both a title and a prompt.'));
				loading = false;
				return;
			}
			if (prompts.length === 0) {
				info.meta.suggestion_prompts = null;
			}
		}

		info.params.stop = params.stop ? params.stop.split(',').filter((s) => s.trim()) : null;
		Object.keys(info.params).forEach((key) => {
			if (info.params[key] === '' || info.params[key] === null) {
				delete info.params[key];
			}
		});

		// llens(#43): id が変わったら recreate(旧 id 削除→新 id 作成)。confirm を挟む。
		if (edit && id !== originalId) {
			loading = false;
			showRenameConfirm = true;
			return;
		}

		await doSubmit();
	};

	const doSubmit = async () => {
		loading = true;
		await onSubmit(info);
		loading = false;
		success = false;
	};

	onMount(async () => {
		// 生モデル(接続由来のベースモデル)一覧。/api/models/base は admin ゲート。
		// 既定では preset 作成/編集は admin のみ(workspace.models 既定 False)。
		// 非 admin では 403 を投げるため、空配列にフォールバックしてエディタを壊さない。
		baseModels = (await getModels(true).catch(() => [])) ?? [];

		await tools.set(await getTools());
		await skills.set(await getSkills());
		await functions.set(await getFunctions());

		// Scroll to top 'workspace-container' element
		const workspaceContainer = document.getElementById('workspace-container');
		if (workspaceContainer) {
			workspaceContainer.scrollTop = 0;
		}

		if (model) {
			name = model.name;
			await tick();

			id = model.id;
			originalId = id;

			if (model.base_model_id) {
				const base_model = baseModels.find((m) =>
					[model.base_model_id, `${model.base_model_id}:latest`].includes(m.id)
				);

				console.log('base_model', base_model);

				if (base_model) {
					model.base_model_id = base_model.id;
				} else {
					model.base_model_id = null;
				}
			}

			params = { ...params, ...model?.params };
			params.stop = params?.stop
				? (typeof params.stop === 'string' ? params.stop.split(',') : (params?.stop ?? [])).join(
						','
					)
				: null;

			toolIds = model?.meta?.toolIds ?? [];
			skillIds = model?.meta?.skillIds ?? [];
			featureSkills = (model?.meta?.features?.skills ?? true) !== false;
			filterIds = model?.meta?.filterIds ?? [];
			actionIds = model?.meta?.actionIds ?? [];
			capabilities = { ...capabilities, ...(model?.meta?.capabilities ?? {}) };

			if ('access_control' in model) {
				accessControl = model.access_control;
			} else {
				accessControl = {};
			}

			console.log(model?.access_control);
			console.log(accessControl);

			info = {
				...info,
				...JSON.parse(
					JSON.stringify(
						model
							? model
							: {
									id: model.id,
									name: model.name
								}
					)
				)
			};

			console.log(model);
		}

		// タブ切替の暗黙リセット用に、ロード直後の状態を控える
		captureSnapshot();
		loaded = true;
	});
</script>

{#if loaded}
	{#if onBack}
		<button
			class="flex space-x-1"
			on:click={() => {
				onBack();
			}}
		>
			<div class=" self-center">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="h-4 w-4"
				>
					<path
						fill-rule="evenodd"
						d="M17 10a.75.75 0 01-.75.75H5.612l4.158 3.96a.75.75 0 11-1.04 1.08l-5.5-5.25a.75.75 0 010-1.08l5.5-5.25a.75.75 0 111.04 1.08L5.612 9.25H16.25A.75.75 0 0117 10z"
						clip-rule="evenodd"
					/>
				</svg>
			</div>
			<div class=" self-center text-sm font-medium">{'Back'}</div>
		</button>
	{/if}

	<div class="w-full max-h-full flex justify-center">
		<input
			bind:this={filesInputElement}
			bind:files={inputFiles}
			type="file"
			hidden
			accept="image/*"
			on:change={() => {
				let reader = new FileReader();
				reader.onload = (event) => {
					let originalImageUrl = `${event.target.result}`;

					const img = new Image();
					img.src = originalImageUrl;

					img.onload = function () {
						const canvas = document.createElement('canvas');
						const ctx = canvas.getContext('2d');

						// Calculate the aspect ratio of the image
						const aspectRatio = img.width / img.height;

						// Calculate the new width and height to fit within 100x100
						let newWidth, newHeight;
						if (aspectRatio > 1) {
							newWidth = 250 * aspectRatio;
							newHeight = 250;
						} else {
							newWidth = 250;
							newHeight = 250 / aspectRatio;
						}

						// Set the canvas size
						canvas.width = 250;
						canvas.height = 250;

						// Calculate the position to center the image
						const offsetX = (250 - newWidth) / 2;
						const offsetY = (250 - newHeight) / 2;

						// Draw the image on the canvas
						ctx.drawImage(img, offsetX, offsetY, newWidth, newHeight);

						// Get the base64 representation of the compressed image
						const compressedSrc = canvas.toDataURL();

						// Display the compressed image
						info.meta.profile_image_url = compressedSrc;

						inputFiles = null;
						filesInputElement.value = '';
					};
				};

				if (
					inputFiles &&
					inputFiles.length > 0 &&
					['image/gif', 'image/webp', 'image/jpeg', 'image/png', 'image/svg+xml'].includes(
						inputFiles[0]['type']
					)
				) {
					reader.readAsDataURL(inputFiles[0]);
				} else {
					console.log(`Unsupported File Type '${inputFiles[0]['type']}'.`);
					inputFiles = null;
				}
			}}
		/>

		{#if !edit || (edit && model)}
			<!-- llens(#34): 設定画面と同型 — 左サブメニュー + 右コンテンツ(max-w、親 justify-center で中央寄せ) -->
			<form
				class="flex flex-col lg:flex-row w-full gap-3 lg:gap-4"
				on:submit|preventDefault={() => {
					submitHandler();
				}}
			>
				<!-- 左サブメニュー(縦タブ)— /workspace/settings と同じ様式 + アイコン -->
				<div
					class="tabs flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-40 dark:text-gray-200 text-sm font-medium text-left scrollbar-none"
				>
					<button
						class="px-0.5 py-1 min-w-fit rounded-lg flex-1 lg:flex-none flex text-left transition {selectedTab ===
						'general'
							? ''
							: ' text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
						type="button"
						on:click={() => switchTab('general')}
					>
						<div class=" self-center mr-2">
							<span class="i-lucide-settings size-4 block"></span>
						</div>
						<div class=" self-center">{$i18n.t('General')}</div>
					</button>

					<button
						class="px-0.5 py-1 min-w-fit rounded-lg flex-1 lg:flex-none flex text-left transition {selectedTab ===
						'prompt'
							? ''
							: ' text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
						type="button"
						on:click={() => switchTab('prompt')}
					>
						<div class=" self-center mr-2">
							<span class="i-lucide-file-text size-4 block"></span>
						</div>
						<div class=" self-center">{$i18n.t('Prompt')}</div>
					</button>

					<button
						class="px-0.5 py-1 min-w-fit rounded-lg flex-1 lg:flex-none flex text-left transition {selectedTab ===
						'advanced'
							? ''
							: ' text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
						type="button"
						on:click={() => switchTab('advanced')}
					>
						<div class=" self-center mr-2">
							<span class="i-lucide-sliders-horizontal size-4 block"></span>
						</div>
						<div class=" self-center">{$i18n.t('Advanced Params')}</div>
					</button>
				</div>

				<!-- 右コンテンツ。名前/ID は全タブ共通ヘッダとしてここに置く -->
				<div class="flex-1 min-w-0">
					<div class="max-w-3xl mx-auto">
						<div class="mb-3 flex flex-col">
							<input
								class="text-3xl font-semibold w-full bg-transparent outline-hidden"
								placeholder={$i18n.t('Model Name')}
								bind:value={name}
								required
							/>
							<input
								class="text-xs w-full bg-transparent text-gray-500 outline-hidden"
								placeholder={$i18n.t('Model ID')}
								bind:value={id}
								required
							/>
						</div>

						{#if selectedTab === 'general'}
							<div class="flex flex-col sm:flex-row gap-4 my-2">
								<div class="shrink-0">
									<div class="self-center">
										<button
											class="rounded-xl flex shrink-0 items-center {info.meta.profile_image_url
												? 'bg-transparent'
												: 'bg-white'} shadow-xl group relative"
											type="button"
											on:click={() => {
												filesInputElement.click();
											}}
										>
											{#if info.meta.profile_image_url}
												<img
													src={info.meta.profile_image_url}
													alt="model profile"
													class="rounded-xl size-72 md:size-60 object-cover shrink-0"
												/>
											{:else}
												<img
													src={DEFAULT_MODEL_IMAGE_URL}
													alt="model profile"
													class=" rounded-xl size-72 md:size-60 object-cover shrink-0"
												/>
											{/if}

											<div class="absolute bottom-0 right-0 z-10">
												<div class="m-1.5">
													<div
														class="shadow-xl p-1 rounded-full border-2 border-white bg-gray-800 text-white group-hover:bg-gray-600 transition dark:border-black dark:bg-white dark:group-hover:bg-gray-200 dark:text-black"
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 16 16"
															fill="currentColor"
															class="size-5"
														>
															<path
																fill-rule="evenodd"
																d="M2 4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4Zm10.5 5.707a.5.5 0 0 0-.146-.353l-1-1a.5.5 0 0 0-.708 0L9.354 9.646a.5.5 0 0 1-.708 0L6.354 7.354a.5.5 0 0 0-.708 0l-2 2a.5.5 0 0 0-.146.353V12a.5.5 0 0 0 .5.5h8a.5.5 0 0 0 .5-.5V9.707ZM12 5a1 1 0 1 1-2 0 1 1 0 0 1 2 0Z"
																clip-rule="evenodd"
															/>
														</svg>
													</div>
												</div>
											</div>

											<div
												class="absolute top-0 bottom-0 left-0 right-0 bg-white dark:bg-black rounded-lg opacity-0 group-hover:opacity-20 transition"
											></div>
										</button>

										<div class="flex w-full mt-1 justify-end">
											<button
												class="px-2 py-1 text-gray-500 rounded-lg text-xs"
												on:click={() => {
													info.meta.profile_image_url = null;
												}}
												type="button"
											>
												Reset Image</button
											>
										</div>
									</div>
								</div>
								<div class="flex-1 min-w-0">
									{#if preset}
										<div class="my-1">
											<div class=" text-sm font-semibold mb-1">{$i18n.t('Base Model (From)')}</div>

											<div>
												<select
													class="text-sm w-full bg-transparent outline-hidden"
													placeholder="Select a base model (e.g. llama3, gpt-4o)"
													bind:value={info.base_model_id}
													on:change={(e) => {
														addUsage(e.target.value);
													}}
													required
												>
													<option value={null} class=" text-gray-900"
														>{$i18n.t('Select a base model')}</option
													>
													{#each baseModels.filter( (m) => (model ? m.id !== model.id : true) ) as model}
														<option value={model.id} class=" text-gray-900">{model.name}</option>
													{/each}
												</select>
											</div>
										</div>
									{/if}

									<div class="my-1">
										<div class="mb-1 flex w-full justify-between items-center">
											<div class=" self-center text-sm font-semibold">{$i18n.t('Description')}</div>
										</div>

										<Textarea
											className=" text-sm w-full bg-transparent outline-hidden resize-none overflow-y-hidden "
											placeholder={$i18n.t('Add a short description about what this model does')}
											bind:value={info.meta.description}
										/>
									</div>

									<div class="my-2">
										<div class="px-3 py-2 bg-gray-50 dark:bg-gray-950 rounded-lg">
											<AccessControl bind:accessControl allowPublic={true} />
										</div>
									</div>
								</div>
							</div>

							<hr class=" border-gray-100 dark:border-gray-850 my-1.5" />

							<div class="my-2">
								<div class="flex w-full justify-between mb-1">
									<div class=" self-center text-sm font-semibold">{$i18n.t('Features')}</div>
								</div>

								<!-- llens(#49): label で行クリックトグル -->
								<label class="flex items-center gap-2 mr-3 cursor-pointer">
									<span class="flex items-center">
										<Checkbox
											state={featureSkills ? 'checked' : 'unchecked'}
											on:change={(e) => {
												featureSkills = e.detail === 'checked';
											}}
										/>
									</span>
									<div class=" py-0.5 text-sm w-full flex items-center gap-1.5">
										<span class="font-medium">{$i18n.t('Skills')}</span>
										<span class="text-xs text-gray-500"
											>{$i18n.t('スキル一覧をモデルに提示(load_skill)+ /command 発火')}</span
										>
									</div>
								</label>

								<div class="mt-2">
									<SkillsSelector
										bind:selectedSkillIds={skillIds}
										skills={$skills ?? []}
										disabled={!featureSkills}
									/>
								</div>
							</div>

							<div class="my-2">
								<ToolsSelector bind:selectedToolIds={toolIds} tools={$tools} />
							</div>

							<div class="my-2">
								<FiltersSelector
									bind:selectedFilterIds={filterIds}
									filters={$functions.filter((func) => func.type === 'filter')}
								/>
							</div>

							<div class="my-2">
								<ActionsSelector
									bind:selectedActionIds={actionIds}
									actions={$functions.filter((func) => func.type === 'action')}
								/>
							</div>

							<div class="my-2">
								<Capabilities bind:capabilities />
							</div>
						{:else if selectedTab === 'prompt'}
							<div class="my-2">
								<div class=" text-sm font-semibold mb-2">{$i18n.t('System Prompt')}</div>
								<div class="rounded-lg border border-gray-100 dark:border-gray-850 overflow-hidden">
									<CodeEditor
										value={info.params.system ?? ''}
										onChange={(value) => {
											info.params.system = value;
										}}
									/>
								</div>
							</div>

							<div class="my-2">
								<div class="flex w-full justify-between items-center">
									<div class=" self-center text-sm font-semibold">
										{$i18n.t('Prompt suggestions')}
									</div>

									<button
										class="p-1 px-2 text-xs flex rounded-sm transition"
										type="button"
										on:click={() => {
											const prompts = info.meta.suggestion_prompts ?? [];
											if (
												prompts.length === 0 ||
												(prompts.at(-1).title !== '' && prompts.at(-1).content !== '')
											) {
												info.meta.suggestion_prompts = [...prompts, { title: '', content: '' }];
											}
										}}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 20 20"
											fill="currentColor"
											class="w-4 h-4"
										>
											<path
												d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z"
											/>
										</svg>
									</button>
								</div>

								<div class="flex flex-col space-y-1.5 mt-1 mb-3">
									{#if (info.meta.suggestion_prompts ?? []).length > 0}
										{#each info.meta.suggestion_prompts as prompt, promptIdx}
											<div class=" flex border border-gray-100 dark:border-gray-850 rounded-lg">
												<div class="flex flex-col flex-1">
													<input
														class=" text-sm px-2 py-1 w-full bg-transparent outline-hidden border-b border-gray-100 dark:border-gray-850"
														placeholder={$i18n.t('Title (e.g. Greeting)')}
														bind:value={prompt.title}
														required
													/>
													<input
														class=" text-sm px-2 py-1 w-full bg-transparent outline-hidden"
														placeholder={$i18n.t(
															'Prompt (e.g. Who are you? Show me a quick demo.)'
														)}
														bind:value={prompt.content}
														required
													/>
												</div>

												<button
													class="px-2"
													type="button"
													on:click={() => {
														info.meta.suggestion_prompts.splice(promptIdx, 1);
														info.meta.suggestion_prompts = info.meta.suggestion_prompts;
													}}
												>
													<svg
														xmlns="http://www.w3.org/2000/svg"
														viewBox="0 0 20 20"
														fill="currentColor"
														class="w-4 h-4"
													>
														<path
															d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
														/>
													</svg>
												</button>
											</div>
										{/each}
									{:else}
										<div class="text-xs text-center text-gray-500 py-2">
											{$i18n.t('No prompt suggestions')}
										</div>
									{/if}
								</div>
							</div>
						{:else if selectedTab === 'advanced'}
							<div class="my-2">
								<AdvancedParams
									admin={true}
									bind:params
									on:change={(e) => {
										info.params = { ...info.params, ...params };
									}}
								/>
							</div>
						{/if}

						<div class="my-4 flex justify-start pb-20">
							<button
								class=" text-sm px-3 py-2 transition rounded-lg {loading
									? ' cursor-not-allowed bg-black hover:bg-gray-900 text-white dark:bg-white dark:hover:bg-gray-100 dark:text-black'
									: 'bg-black hover:bg-gray-900 text-white dark:bg-white dark:hover:bg-gray-100 dark:text-black'} flex justify-center"
								type="submit"
								disabled={loading}
							>
								<div class=" self-center font-medium">
									{#if edit}
										{$i18n.t('Save & Update')}
									{:else}
										{$i18n.t('Save & Create')}
									{/if}
								</div>

								{#if loading}
									<div class="ml-1.5 self-center">
										<svg
											class=" w-4 h-4"
											viewBox="0 0 24 24"
											fill="currentColor"
											xmlns="http://www.w3.org/2000/svg"
											><style>
												.spinner_ajPY {
													transform-origin: center;
													animation: spinner_AtaB 0.75s infinite linear;
												}
												@keyframes spinner_AtaB {
													100% {
														transform: rotate(360deg);
													}
												}
											</style><path
												d="M12,1A11,11,0,1,0,23,12,11,11,0,0,0,12,1Zm0,19a8,8,0,1,1,8-8A8,8,0,0,1,12,20Z"
												opacity=".25"
											/><path
												d="M10.14,1.16a11,11,0,0,0-9,8.92A1.59,1.59,0,0,0,2.46,12,1.52,1.52,0,0,0,4.11,10.7a8,8,0,0,1,6.66-6.61A1.42,1.42,0,0,0,12,2.69h0A1.57,1.57,0,0,0,10.14,1.16Z"
												class="spinner_ajPY"
											/></svg
										>
									</div>
								{/if}
							</button>
						</div>
					</div>
				</div>
			</form>
		{/if}
	</div>
{/if}

<ConfirmDialog
	bind:show={showRenameConfirm}
	title={$i18n.t('Change ID?')}
	on:confirm={() => {
		doSubmit();
	}}
>
	<div class="text-sm text-gray-500">
		<div class=" bg-yellow-500/20 text-yellow-700 dark:text-yellow-200 rounded-lg px-4 py-3">
			<div>
				{$i18n.t('The ID will be changed by deleting the old one and creating a new one.')}
			</div>
			<ul class=" mt-1 list-disc pl-4 text-xs">
				<li>{$i18n.t('Assignments and references from other items will be detached.')}</li>
			</ul>
		</div>
	</div>
</ConfirmDialog>
