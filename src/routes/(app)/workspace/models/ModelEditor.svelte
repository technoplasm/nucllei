<script lang="ts">
	// llens: workspace/models(preset 編集)のほか workspace/Settings/Models(生モデル編集、
	// preset=false + onBack)からも使う。models 専用素材として route 配下へコロケート(#70 A2)。
	import { onMount, getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { tools, functions, skills } from '$lib/stores';
	import { getModels } from '$lib/apis';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { getFunctions } from '$lib/apis/functions';

	import AdvancedParams from './AdvancedParams.svelte';
	import ToolsSelector from './ToolsSelector.svelte';
	import SkillsSelector from './SkillsSelector.svelte';
	import FunctionsSelector from './FunctionsSelector.svelte';
	import Capabilities from './Capabilities.svelte';
	import AccessControl from '$lib/components/workspace/common/AccessControl.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	const i18n = getContext('i18n');

	let {
		onSubmit,
		onBack = null,
		model = null,
		edit = false,
		preset = true
	}: {
		onSubmit: (info: any) => void | Promise<void>;
		onBack?: null | (() => void);
		model?: any;
		edit?: boolean;
		preset?: boolean;
	} = $props();

	// 基本設定 | プロンプト | 高度なパラメータ(Admin > Users の様式)
	let selectedTab = $state('general');

	let loading = $state(false);
	let loaded = $state(false);

	let filesInputElement = $state<HTMLInputElement>();
	let inputFiles = $state<FileList | null>(null);

	let id = $state('');
	let name = $state('');

	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	let originalId = '';
	let showRenameConfirm = $state(false);

	// 作成時は表示名から id を自動生成する
	$effect(() => {
		if (!edit && name) {
			id = name
				.replace(/\s+/g, '-')
				.replace(/[^a-zA-Z0-9-]/g, '')
				.toLowerCase();
		}
	});

	let info = $state<any>({
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
	});

	let params = $state<Record<string, any>>({
		system: ''
	});
	// llens(#70): vision は既定 OFF(2026-07-15。画像入力は明示的に有効化したモデルだけ)
	let capabilities = $state<Record<string, boolean | undefined>>({
		vision: false,
		usage: undefined,
		citations: true
	});

	let toolIds = $state<string[]>([]);
	let skillIds = $state<string[]>([]);
	let filterIds = $state<string[]>([]);
	let actionIds = $state<string[]>([]);

	// llens(#42): 機能「スキル」(meta.features.skills、既定 ON)。OFF でこのモデルの
	// manifest(load_skill)と /command 発火が両方止まる。
	let featureSkills = $state(true);

	let accessControl = $state<any>({});

	// llens: チャットのモデルピッカー(/api/models = $models)は preset のみを返すよう
	// になったため、preset のベースモデル選択には生モデルを含む /api/models/base を使う。
	// admin ゲートのエンドポイントなので admin 前提(workspace.models 既定は False)。
	let baseModels = $state<any[]>([]);

	// llens(#47): タブ切替で「見えていない別タブの編集」をそのまま保存しないよう、ロード時の
	// 状態へ暗黙リセットする。名前/ID は対象外(リセットしない — リネームは submit 時の confirm で扱う)。
	let stateSnapshot: string | null = null;
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
	const switchTab = (tab: string) => {
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

	const addUsage = (base_model_id: string) => {
		const baseModel = baseModels.find((m) => m.id === base_model_id);

		if (baseModel) {
			if (baseModel.owned_by === 'openai') {
				capabilities.usage = baseModel?.meta?.capabilities?.usage ?? false;
			} else {
				delete capabilities.usage;
			}
		}
	};

	const submitHandler = async () => {
		if (id === '') {
			toast.error($i18n.t('Model ID is required.'));
			return;
		}

		if (name === '') {
			toast.error($i18n.t('Model Name is required.'));
			return;
		}

		loading = true;

		info.id = id;
		info.name = name;

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
			if (
				prompts.some((p: any) => (p?.title ?? '').trim() === '' || (p?.content ?? '').trim() === '')
			) {
				toast.error($i18n.t('Prompt suggestions require both a title and a prompt.'));
				loading = false;
				return;
			}
			if (prompts.length === 0) {
				info.meta.suggestion_prompts = null;
			}
		}

		info.params.stop = params.stop ? params.stop.split(',').filter((s: string) => s.trim()) : null;
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
		await onSubmit($state.snapshot(info));
		loading = false;
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

				model.base_model_id = base_model ? base_model.id : null;
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

			accessControl = 'access_control' in model ? model.access_control : {};

			info = {
				...info,
				...JSON.parse(JSON.stringify(model))
			};
		}

		// タブ切替の暗黙リセット用に、ロード直後の状態を控える
		captureSnapshot();
		loaded = true;
	});
</script>

{#if loaded}
	{#if onBack}
		<button
			class="inline-flex items-center gap-1 text-sm py-0.5 pl-2 pr-4 mb-2 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg transition"
			type="button"
			onclick={() => {
				onBack();
			}}
		>
			<span class="i-lucide-chevron-left size-4 block"></span>
			{$i18n.t('Back')}
		</button>
	{:else}
		<!-- workspace/models からの利用(create/edit ページ)— 一覧へ戻る(SkillEditor 同型) -->
		<a
			class="inline-flex items-center gap-1 text-sm py-0.5 pl-2 pr-4 mb-2 bg-secondary hover:bg-secondary-hover text-secondary-content rounded-lg transition"
			href="/workspace/models"
		>
			<span class="i-lucide-chevron-left size-4 block"></span>
			{$i18n.t('Back to list')}
		</a>
	{/if}

	<div class="w-full max-h-full flex justify-center">
		<input
			bind:this={filesInputElement}
			bind:files={inputFiles}
			type="file"
			hidden
			accept="image/*"
			onchange={() => {
				let reader = new FileReader();
				reader.onload = (event) => {
					let originalImageUrl = `${event.target?.result}`;

					const img = new Image();
					img.src = originalImageUrl;

					img.onload = function () {
						const canvas = document.createElement('canvas');
						const ctx = canvas.getContext('2d');
						if (!ctx) return;

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
						if (filesInputElement) {
							filesInputElement.value = '';
						}
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
					inputFiles = null;
				}
			}}
		/>

		{#if !edit || (edit && model)}
			<!-- llens(#34): 設定画面と同型 — 左サブメニュー + 右コンテンツ。
			     横幅は workspace/+layout.svelte 側で一元管理(5xl 中央寄せ)するのでここでは持たない。
			     lg 未満は flex-col で submenu が上に逃げる。 -->
			<form
				class="flex flex-col lg:flex-row w-full gap-3 lg:gap-4"
				onsubmit={(e) => {
					e.preventDefault();
					submitHandler();
				}}
			>
				<!-- 左サブメニュー(縦タブ)— /workspace/settings と同じ様式 + アイコン -->
				<div
					class="tabs flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-40 text-content text-sm font-medium text-left scrollbar-none"
				>
					<button
						class="px-0.5 py-1 min-w-fit rounded-lg flex-none flex text-left transition {selectedTab ===
						'general'
							? ''
							: ' text-content-subtle hover:text-content'}"
						type="button"
						onclick={() => switchTab('general')}
					>
						<div class=" self-center mr-2">
							<span class="i-lucide-settings size-4 block"></span>
						</div>
						<div class=" self-center">{$i18n.t('General')}</div>
					</button>

					<button
						class="px-0.5 py-1 min-w-fit rounded-lg flex-none flex text-left transition {selectedTab ===
						'prompt'
							? ''
							: ' text-content-subtle hover:text-content'}"
						type="button"
						onclick={() => switchTab('prompt')}
					>
						<div class=" self-center mr-2">
							<span class="i-lucide-file-text size-4 block"></span>
						</div>
						<div class=" self-center">{$i18n.t('Prompt')}</div>
					</button>

					<button
						class="px-0.5 py-1 min-w-fit rounded-lg flex-none flex text-left transition {selectedTab ===
						'advanced'
							? ''
							: ' text-content-subtle hover:text-content'}"
						type="button"
						onclick={() => switchTab('advanced')}
					>
						<div class=" self-center mr-2">
							<span class="i-lucide-sliders-horizontal size-4 block"></span>
						</div>
						<div class=" self-center">{$i18n.t('Advanced Params')}</div>
					</button>
				</div>

				<!-- 右コンテンツ。横幅は layout 側で管理。 -->
				<div class="flex-1 min-w-0">
					<div>
						{#if selectedTab === 'general'}
							<!-- llens(#70): 名前/ID/Base Model を左カラムに、モデル画像は右端へ。
							     画像は行ブロックの高さに合わせた正方形(max-h-40 キャップ)。absolute で
							     行高に寄与させず、左カラムの mr-44(= 最大画像幅 + 余白)で幅を予約する。 -->
							<div class="relative my-2">
								<div class="mr-44 flex flex-col gap-2">
									<div>
										<label class="block text-sm font-semibold mb-1" for="model-name">
											{$i18n.t('Name')}
										</label>
										<input
											id="model-name"
											class="text-sm w-full px-2 py-1 bg-surface-raised rounded-lg outline-hidden"
											placeholder={$i18n.t('Model Name')}
											bind:value={name}
											required
										/>
									</div>

									<div>
										<label class="block text-sm font-semibold mb-1" for="model-id">
											{$i18n.t('ID')}
										</label>
										<input
											id="model-id"
											class="text-sm w-full px-2 py-1 bg-surface-raised rounded-lg outline-hidden"
											placeholder={$i18n.t('Model ID')}
											bind:value={id}
											required
										/>
									</div>

									{#if preset}
										<div>
											<label class="block text-sm font-semibold mb-1" for="model-base-model">
												{$i18n.t('Base Model (From)')}
											</label>

											<select
												id="model-base-model"
												class="text-sm w-full max-w-sm px-2 py-1 bg-surface-raised rounded-lg outline-hidden"
												bind:value={info.base_model_id}
												onchange={(e) => {
													addUsage(e.currentTarget.value);
												}}
												required
											>
												<option value={null} class=" text-content"
													>{$i18n.t('Select a base model')}</option
												>
												{#each baseModels.filter( (m) => (model ? m.id !== model.id : true) ) as baseModel (baseModel.id)}
													<option value={baseModel.id} class=" text-content"
														>{baseModel.name}</option
													>
												{/each}
											</select>
										</div>
									{/if}
								</div>

								<div class="absolute inset-y-0 right-0 flex flex-col items-end">
									<button
										class="relative group flex-1 min-h-0 max-h-40 aspect-square rounded-xl overflow-hidden shadow-lg {info
											.meta.profile_image_url
											? 'bg-transparent'
											: 'bg-white'}"
										type="button"
										aria-label={$i18n.t('Change image')}
										onclick={() => {
											filesInputElement?.click();
										}}
									>
										<!-- img は absolute にして行の高さへ寄与させない(高さは左カラム由来のみ) -->
										<img
											src={info.meta.profile_image_url ?? DEFAULT_MODEL_IMAGE_URL}
											alt="model profile"
											class="absolute inset-0 size-full object-cover"
										/>

										<div class="absolute bottom-0 right-0 z-10 m-1">
											<div
												class="shadow-xl p-1 rounded-full border-2 border-white bg-gray-800 text-white group-hover:bg-gray-600 transition dark:border-black dark:bg-white dark:group-hover:bg-gray-200 dark:text-black"
											>
												<span class="i-lucide-camera size-4 block"></span>
											</div>
										</div>

										<div
											class="absolute inset-0 bg-white dark:bg-black opacity-0 group-hover:opacity-20 transition"
										></div>
									</button>

									<button
										class="px-2 py-1 text-content-muted hover:text-content rounded-lg text-xs"
										onclick={() => {
											info.meta.profile_image_url = null;
										}}
										type="button"
									>
										{$i18n.t('Reset Image')}
									</button>
								</div>
							</div>

							<div class="my-2">
								<label class="block text-sm font-semibold mb-1" for="model-description">
									{$i18n.t('Description')}
								</label>

								<Textarea
									id="model-description"
									className="text-sm w-full px-2 py-1 bg-surface-raised rounded-lg outline-hidden resize-none overflow-y-hidden"
									placeholder={$i18n.t('Add a short description about what this model does')}
									bind:value={info.meta.description}
								/>
							</div>

							<div class="my-2">
								<div class="px-3 py-2 bg-surface-raised rounded-lg max-w-xs">
									<AccessControl bind:accessControl allowPublic={true} />
								</div>
							</div>

							<div class="my-2">
								<Capabilities bind:capabilities bind:featureSkills />
							</div>

							<hr class=" border-line my-1.5" />

							<div class="mt-2">
								<SkillsSelector
									bind:selectedSkillIds={skillIds}
									skills={$skills ?? []}
									disabled={!featureSkills}
								/>
							</div>

							<hr class=" border-line my-1.5" />

							<div class="my-2">
								<ToolsSelector bind:selectedToolIds={toolIds} tools={$tools ?? []} />
							</div>

							<hr class=" border-line my-3" />

							<div class="my-2">
								<FunctionsSelector
									bind:selectedFilterIds={filterIds}
									bind:selectedActionIds={actionIds}
									functions={($functions ?? []).filter((func: any) =>
										['filter', 'action'].includes(func.type)
									)}
								/>
							</div>
						{:else if selectedTab === 'prompt'}
							<div class="my-2">
								<div class=" text-sm font-semibold mb-2">{$i18n.t('System Prompt')}</div>
								<div class="rounded-lg border border-line overflow-hidden">
									<CodeEditor
										value={info.params.system ?? ''}
										onChange={(value: string) => {
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
										class="p-1 px-2 flex rounded-lg text-content-muted hover:text-content transition"
										type="button"
										aria-label={$i18n.t('Add prompt suggestion')}
										onclick={() => {
											const prompts = info.meta.suggestion_prompts ?? [];
											if (
												prompts.length === 0 ||
												(prompts.at(-1).title !== '' && prompts.at(-1).content !== '')
											) {
												info.meta.suggestion_prompts = [...prompts, { title: '', content: '' }];
											}
										}}
									>
										<span class="i-lucide-plus size-4 block"></span>
									</button>
								</div>

								<div class="flex flex-col space-y-1.5 mt-1 mb-3">
									{#if (info.meta.suggestion_prompts ?? []).length > 0}
										{#each info.meta.suggestion_prompts as prompt, promptIdx}
											<div class=" flex border border-line rounded-lg">
												<div class="flex flex-col flex-1">
													<input
														class=" text-sm px-2 py-1 w-full bg-transparent outline-hidden border-b border-line"
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
													class="px-2 text-content-muted hover:text-content"
													type="button"
													aria-label={$i18n.t('Delete')}
													onclick={() => {
														info.meta.suggestion_prompts.splice(promptIdx, 1);
													}}
												>
													<span class="i-lucide-x size-4 block"></span>
												</button>
											</div>
										{/each}
									{:else}
										<div class="text-xs text-center text-content-subtle py-2">
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
									on:change={() => {
										info.params = { ...info.params, ...params };
									}}
								/>
							</div>
						{/if}

						<div class="my-4 flex justify-start pb-20">
							<button
								class="px-4 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content transition rounded-lg {loading
									? ' cursor-not-allowed opacity-50'
									: ''} flex items-center"
								type="submit"
								disabled={loading}
							>
								{#if edit}
									{$i18n.t('Save & Update')}
								{:else}
									{$i18n.t('Save & Create')}
								{/if}

								{#if loading}
									<span class="i-lucide-loader-circle size-4 block animate-spin ml-1.5"></span>
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
	<div class="text-sm text-content-muted">
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
