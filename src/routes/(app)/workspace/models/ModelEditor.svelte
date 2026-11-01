<script lang="ts">
	// llens: workspace/models の preset 編集専用(#82 で生モデル編集経路を撤去 — モデルは
	// 常に base_model_id を持つ preset)。models 専用素材として route 配下へコロケート(#70 A2)。
	import { onMount, getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { DEFAULT_MODEL_IMAGE_URL } from '$lib/constants';
	import { tools, functions, skills } from '$lib/stores';
	import { getModels } from '$lib/apis';
	import { getModels as getWorkspaceModels } from '$lib/apis/models';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { getFunctions } from '$lib/apis/functions';
	import { isToolReachableFromPreset } from '$lib/utils/skills';

	import AdvancedParams from './AdvancedParams.svelte';
	import ToolsSelector from './ToolsSelector.svelte';
	import SkillsSelector from './SkillsSelector.svelte';
	import SubagentPresetsSelector from './SubagentPresetsSelector.svelte';
	import FunctionsSelector from './FunctionsSelector.svelte';
	import Capabilities from './Capabilities.svelte';
	import SystemPromptPreview from './SystemPromptPreview.svelte';
	import PreamblePreview from './PreamblePreview.svelte';
	import AccessControl from '$lib/components/workspace/common/AccessControl.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	const i18n = getContext('i18n');

	let {
		onSubmit,
		model = null,
		edit = false
	}: {
		onSubmit: (info: any) => void | Promise<void>;
		model?: any;
		edit?: boolean;
	} = $props();

	// 一般 | システムプロンプト | 会話の前置き | プロンプトの提案 | 高度なパラメータ(Admin > Users の様式)
	let selectedTab = $state('general');
	const tabs = $derived([
		{ id: 'general', icon: 'i-lucide-settings', label: $i18n.t('General') },
		{ id: 'system', icon: 'i-lucide-file-text', label: $i18n.t('System Prompt') },
		{
			id: 'context',
			icon: 'i-lucide-message-square-quote',
			label: $i18n.t('Conversation preamble')
		},
		// チャット画面の提案の見出し (Suggestions.svelte) と同じアイコン
		{ id: 'suggestions', icon: 'i-lucide-zap', label: $i18n.t('Prompt suggestions') },
		{ id: 'advanced', icon: 'i-lucide-sliders-horizontal', label: $i18n.t('Advanced Params') }
	]);
	// システムプロンプト・会話の前置きの欄: それぞれ 編集 | プレビュー
	let systemView = $state<'edit' | 'preview'>('edit');
	let contextView = $state<'edit' | 'preview'>('edit');
	// エディタを残り全高に貼るタブ
	const fullHeightTab = $derived(selectedTab === 'system' || selectedTab === 'context');

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
			suggestion_prompts: null
		},
		params: {
			system: ''
		}
	});

	let params = $state<Record<string, any>>({
		system: ''
	});
	// llens(#70): vision は既定 OFF(2026-07-15。画像入力は明示的に有効化したモデルだけ)。
	// usage は既定 ON — stream_options を弾く互換 API 向けの opt-out(backend の
	// is_usage_requested と対。旧 addUsage の owned_by 分岐は廃止)。
	// preserve_reasoning は既定 ON — tool ループの追撃に同じターンの reasoning_content を
	// 載せ直す(backend の is_reasoning_preserved と対、2026-10-19)。
	let capabilities = $state<Record<string, boolean | undefined>>({
		vision: false,
		usage: true,
		preserve_reasoning: true
	});

	let toolIds = $state<string[]>([]);
	let skillIds = $state<string[]>([]);
	let subagentPresetIds = $state<string[]>([]);
	let filterIds = $state<string[]>([]);
	let actionIds = $state<string[]>([]);

	// llens(#42): 機能「スキル」(meta.features.skills、既定 ON)。OFF でこのモデルの
	// manifest(load_skill)と /command 発火が両方止まる。
	let featureSkills = $state(true);
	// 機能「レシピ」(meta.features.recipes、既定 ON)。OFF でこのモデルにレシピの tool と一覧が入らない
	let featureRecipes = $state(true);

	let accessControl = $state<any>({});

	// llens: チャットのモデルピッカー(/api/models = $models)は preset のみを返すよう
	// になったため、preset のベースモデル選択には生モデルを含む /api/models/base を使う。
	// admin ゲートのエンドポイントなので admin 前提(workspace.models 既定は False)。
	let baseModels = $state<any[]>([]);

	// llens(#107): サブエージェント委任先の候補(preset のみ = /api/v1/models/、
	// workspace 一覧と同じ供給元)。picker 用の $models ストアは使わない。
	let workspaceModels = $state<any[]>([]);

	// llens(#47): タブ切替で「見えていない別タブの編集」をそのまま保存しないよう、ロード時の
	// 状態へ暗黙リセットする。名前/ID は対象外(リセットしない — リネームは submit 時の confirm で扱う)。
	// システムプロンプト・会話の前置き・プロンプトの提案は 1 組: この 3 タブの間の切替ではリセットせず、
	// 保存すると 3 つとも入る。組の外へ出るとき (一般・高度なパラメータとの行き来) は 3 つまとめてリセットする。
	const TEXT_GROUP_TABS = ['system', 'context', 'suggestions'];
	let stateSnapshot: string | null = null;
	// 組の各タブの値 (比較用の文字列)。ロード時の値と比べて、見えていないタブに未保存の変更が
	// あるかをタブ名の横の印で出す
	const textTabValue = (tab: string) => {
		if (tab === 'system') return info?.params?.system ?? '';
		if (tab === 'context') return info?.params?.context ?? '';
		return JSON.stringify(info?.meta?.suggestion_prompts ?? []);
	};
	let loadedTextTabs = $state<Record<string, string>>({});
	const dirtyTextTabs = $derived(
		loaded ? TEXT_GROUP_TABS.filter((tab) => textTabValue(tab) !== loadedTextTabs[tab]) : []
	);
	const captureSnapshot = () => {
		loadedTextTabs = Object.fromEntries(TEXT_GROUP_TABS.map((tab) => [tab, textTabValue(tab)]));
		stateSnapshot = JSON.stringify({
			info,
			params,
			capabilities,
			toolIds,
			skillIds,
			subagentPresetIds,
			filterIds,
			actionIds,
			featureSkills,
			featureRecipes,
			accessControl
		});
	};
	// プレビューに渡す編集中 (保存前) のモデル
	const previewModelForm = () => ({
		...$state.snapshot(info),
		id: id || info.id,
		name: name || info.name
	});

	const switchTab = (tab: string) => {
		if (tab === selectedTab) return;
		const withinTextGroup = TEXT_GROUP_TABS.includes(tab) && TEXT_GROUP_TABS.includes(selectedTab);
		if (stateSnapshot && !withinTextGroup) {
			const s = JSON.parse(stateSnapshot);
			info = s.info;
			params = s.params;
			capabilities = s.capabilities;
			toolIds = s.toolIds;
			skillIds = s.skillIds;
			subagentPresetIds = s.subagentPresetIds;
			filterIds = s.filterIds;
			actionIds = s.actionIds;
			featureSkills = s.featureSkills;
			featureRecipes = s.featureRecipes;
			accessControl = s.accessControl;
		}
		selectedTab = tab;
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

		if (subagentPresetIds.length > 0) {
			info.meta.subagentPresetIds = subagentPresetIds;
		} else {
			if (info.meta.subagentPresetIds) {
				delete info.meta.subagentPresetIds;
			}
		}

		// 既定(ON)のときはキーを残さない(meta を汚さない)
		for (const [key, on] of [
			['skills', featureSkills],
			['recipes', featureRecipes]
		] as const) {
			if (!on) {
				info.meta.features = { ...(info.meta.features ?? {}), [key]: false };
			} else if (info.meta.features?.[key] !== undefined) {
				delete info.meta.features[key];
				if (Object.keys(info.meta.features).length === 0) {
					delete info.meta.features;
				}
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

		// llens(#107): サブエージェント preset 候補。/api/v1/models/ は get_verified_user ゲートで
		// 非 admin も read ACL の範囲で取得できる(通信失敗等に備えた空配列フォールバックのみ)
		workspaceModels = (await getWorkspaceModels().catch(() => [])) ?? [];

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

			params = { ...params, ...model?.params };
			params.stop = params?.stop
				? (typeof params.stop === 'string' ? params.stop.split(',') : (params?.stop ?? [])).join(
						','
					)
				: null;

			toolIds = model?.meta?.toolIds ?? [];
			skillIds = model?.meta?.skillIds ?? [];
			subagentPresetIds = model?.meta?.subagentPresetIds ?? [];
			featureSkills = (model?.meta?.features?.skills ?? true) !== false;
			featureRecipes = (model?.meta?.features?.recipes ?? true) !== false;
			filterIds = model?.meta?.filterIds ?? [];
			actionIds = model?.meta?.actionIds ?? [];
			capabilities = { ...capabilities, ...(model?.meta?.capabilities ?? {}) };

			accessControl = 'access_control' in model ? model.access_control : {};

			info = {
				...info,
				...JSON.parse(JSON.stringify(model))
			};

			// ベースモデルは一覧の id に合わせる (`:latest` 付きで登録されたもの)。一覧に無ければ未選択。
			// model は呼び出し側の state なので書き換えず、編集用の写し (info) にだけ入れる
			if (model.base_model_id) {
				const baseModel = baseModels.find((m) =>
					[model.base_model_id, `${model.base_model_id}:latest`].includes(m.id)
				);
				info.base_model_id = baseModel ? baseModel.id : null;
			}
		}

		// タブ切替の暗黙リセット用に、ロード直後の状態を控える
		captureSnapshot();
		loaded = true;
	});
</script>

<!-- Ctrl+S: システムプロンプト・会話の前置きタブは CodeEditor が拾う。エディタの無いプロンプトの提案タブは
     ここで拾う (どのタブでも保存は 1 回) -->
<svelte:document
	onkeydown={(e) => {
		if (selectedTab === 'suggestions' && (e.ctrlKey || e.metaKey) && e.key === 's') {
			e.preventDefault();
			submitHandler();
		}
	}}
/>

{#if loaded}
	<!-- 全高を戻るリンク + エディタ本体で分ける(SkillEditor 系の h-full 貼りの前提) -->
	<div class="flex flex-col h-full">
		<!-- 一覧へ戻る(SkillEditor 同型) -->
		<a
			class="self-start inline-flex items-center text-sm py-0.5 mb-2 text-content-subtle hover:text-content font-medium"
			href="/workspace/models"
		>
			<span class="i-lucide-arrow-left size-4 mr-2"></span>
			{$i18n.t('Back to list')}
		</a>

		<!-- 高さは settings と同型: 全体を h-full で貼り、スクロールは右コンテンツ列の内部で行う
	 (プロンプト・会話の前置きタブの CodeEditor を残り全高に貼るため)。tabs は shrink-0 で保護済み —
	     素の overflow-x-auto は min-height 0 扱いで高さ 0 に潰れる(#47 の罠)。 -->
		<div class="w-full flex-1 min-h-0 flex justify-center">
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
					class="flex flex-col lg:flex-row w-full h-full gap-3 lg:gap-4"
					onsubmit={(e) => {
						e.preventDefault();
						submitHandler();
					}}
				>
					<!-- 左サブメニュー(縦タブ)— /workspace/settings と同じ様式 + アイコン -->
					<div
						class="tabs shrink-0 flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-44 text-content text-sm font-medium text-left scrollbar-none"
					>
						{#each tabs as tab (tab.id)}
							<button
								class="px-0.5 py-1 min-w-fit rounded-lg flex-none flex text-left {selectedTab ===
								tab.id
									? ''
									: ' text-content-subtle hover:text-content'}"
								type="button"
								onclick={() => switchTab(tab.id)}
							>
								<div class=" self-center mr-2">
									<span class="{tab.icon} size-4 block"></span>
								</div>
								<div class=" self-center whitespace-nowrap">{tab.label}</div>
								<!-- 未保存の印は組の別のタブに居るときだけ (今見ているタブには出さない) -->
								{#if tab.id !== selectedTab && dirtyTextTabs.includes(tab.id)}
									<span
										class="self-center ml-1.5 size-1.5 rounded-full bg-amber-500"
										title={$i18n.t('Unsaved')}
									></span>
									<span class="sr-only">{$i18n.t('Unsaved')}</span>
								{/if}
							</button>
						{/each}
					</div>

					<!-- 右コンテンツ。横幅は layout 側で管理。縦は内部スクロール(settings 同型)。 -->
					<div class="flex-1 min-w-0 overflow-y-auto pr-1">
						<!-- システムプロンプト・会話の前置きタブだけ全高 flex(CodeEditor を flex-1 で下に伸ばす) -->
						<div class={fullHeightTab ? 'h-full flex flex-col' : ''}>
							{#snippet viewToggle(
								label: string,
								current: 'edit' | 'preview',
								onSelect: (view: 'edit' | 'preview') => void
							)}
								<div
									class="inline-flex gap-0.5 p-0.5 bg-surface-muted rounded-lg"
									role="group"
									aria-label={label}
								>
									{#each [{ view: 'edit', icon: 'i-lucide-pencil', label: $i18n.t('Edit') }, { view: 'preview', icon: 'i-lucide-eye', label: $i18n.t('Preview') }] as segment (segment.view)}
										{@const active = current === segment.view}
										<button
											class="flex items-center gap-1.5 px-3 py-0.5 text-xs font-medium rounded-md transition {active
												? 'bg-surface-raised text-content shadow-sm'
												: 'text-content-muted hover:text-content'}"
											type="button"
											aria-pressed={active}
											onclick={() => onSelect(segment.view as 'edit' | 'preview')}
										>
											<span class="{segment.icon} size-3.5 block"></span>
											{segment.label}
										</button>
									{/each}
								</div>
							{/snippet}

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
												class="text-sm w-full px-2 py-1 bg-surface-form rounded-xs outline-hidden"
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
												class="text-sm w-full px-2 py-1 bg-surface-form rounded-xs outline-hidden"
												placeholder={$i18n.t('Model ID')}
												bind:value={id}
												required
											/>
										</div>

										<div>
											<label class="block text-sm font-semibold mb-1" for="model-base-model">
												{$i18n.t('Base Model (From)')}
											</label>

											<select
												id="model-base-model"
												class="text-sm w-full max-w-sm px-2 py-1 bg-surface-form rounded-xs outline-hidden"
												bind:value={info.base_model_id}
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
													class="shadow-xl p-1 rounded-full border-2 border-white bg-gray-800 text-white group-hover:bg-gray-600 dark:border-black dark:bg-white dark:group-hover:bg-gray-200 dark:text-black"
												>
													<span class="i-lucide-camera size-4 block"></span>
												</div>
											</div>

											<div
												class="absolute inset-0 bg-white dark:bg-black opacity-0 group-hover:opacity-20"
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
										className="text-sm w-full px-2 py-1 bg-surface-form rounded-xs outline-hidden resize-none overflow-y-hidden"
										placeholder={$i18n.t('Add a short description about what this model does')}
										bind:value={info.meta.description}
									/>
								</div>

								<div class="my-2">
									<AccessControl bind:accessControl allowPublic={true} />
								</div>

								<div class="my-2">
									<Capabilities bind:capabilities bind:featureSkills bind:featureRecipes />
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

								<div class="mt-2">
									<SubagentPresetsSelector
										bind:selectedPresetIds={subagentPresetIds}
										presets={workspaceModels}
										toolAssigned={isToolReachableFromPreset('builtin_subagent', {
											toolIds,
											skillIds,
											skillList: $skills,
											featureSkills
										})}
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
							{:else if selectedTab === 'system'}
								<!-- System Prompt(残り全高、tool/skill エディタ同型) -->
								<div class="flex-1 min-h-48 flex flex-col mt-2">
									<div class="flex items-center justify-between gap-2 px-1 mb-1">
										<div class="text-sm font-semibold">{$i18n.t('System Prompt')}</div>
										{@render viewToggle($i18n.t('System Prompt'), systemView, (view) => {
											systemView = view;
										})}
									</div>
									<!-- エディタは隠すだけ(unmount すると CodeEditor の中身を作り直す) -->
									<div
										class="flex-1 min-h-0 rounded-xs border border-line overflow-auto {systemView ===
										'edit'
											? ''
											: 'hidden'}"
									>
										<CodeEditor
											id="model-system"
											value={info.params.system ?? ''}
											lang="markdown"
											lineWrapping={true}
											onChange={(value: string) => {
												info.params.system = value;
											}}
											onSave={() => {
												submitHandler();
											}}
										/>
									</div>
									{#if systemView === 'preview'}
										<div class="flex-1 min-h-0 overflow-auto px-1">
											<SystemPromptPreview
												model={previewModelForm}
												hasFilters={filterIds.length > 0}
											/>
										</div>
									{/if}
								</div>
							{:else if selectedTab === 'context'}
								<!-- 説明の下に会話の前置き(残り全高)。Ctrl+S は CodeEditor が document で拾う —
								     エディタはタブごとに 1 つだけマウントされるので、保存も 1 回 -->
								<div class="flex-1 min-h-48 flex flex-col mt-2">
									<div class="flex items-center justify-between gap-2 px-1">
										<div class="text-sm font-semibold">{$i18n.t('Conversation preamble')}</div>
										{@render viewToggle($i18n.t('Conversation preamble'), contextView, (view) => {
											contextView = view;
										})}
									</div>
									<div
										class="px-1 mt-0.5 mb-1 text-xs text-content-muted {contextView === 'edit'
											? ''
											: 'hidden'}"
									>
										{$i18n.t(
											'Goes at the head of the first message, after the system prompt, and stays the same for the whole conversation. Write {{memory}} and {{recipes}} here (they are expanded only here); date, time and user variables belong in the system prompt.',
											{ memory: '{{USER_MEMORY_INDEX}}', recipes: '{{USER_RECIPES}}' }
										)}
									</div>
									<div
										class="flex-1 min-h-0 rounded-xs border border-line overflow-auto {contextView ===
										'edit'
											? ''
											: 'hidden'}"
									>
										<CodeEditor
											id="model-context"
											value={info.params.context ?? ''}
											lang="markdown"
											lineWrapping={true}
											onChange={(value: string) => {
												info.params.context = value;
											}}
											onSave={() => {
												submitHandler();
											}}
										/>
									</div>
									{#if contextView === 'preview'}
										<div class="flex-1 min-h-0 overflow-auto px-1">
											<PreamblePreview model={previewModelForm} />
										</div>
									{/if}
								</div>
							{:else if selectedTab === 'suggestions'}
								<!-- 1 件 = 1 行: タイトル (幅固定) | プロンプト (残り幅、内容に合わせて伸びる)。
								     sm 未満は縦に積む。件数が多いときは右コンテンツ列ごとスクロールする -->
								<div class="my-2">
									<div class="flex w-full justify-between items-center">
										<div class=" self-center text-sm font-semibold">
											{$i18n.t('Prompt suggestions')}
										</div>

										<button
											class="p-1 px-2 flex rounded-lg text-content-muted hover:text-content"
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

									<div class="flex flex-col space-y-1.5 mt-1">
										{#if (info.meta.suggestion_prompts ?? []).length > 0}
											{#each info.meta.suggestion_prompts as prompt, promptIdx}
												<div class=" flex border border-line rounded-lg">
													<div class="flex flex-col sm:flex-row flex-1 min-w-0">
														<input
															class=" text-sm px-2 py-1 w-full sm:w-56 sm:shrink-0 bg-transparent outline-hidden border-b sm:border-b-0 sm:border-r border-line"
															placeholder={$i18n.t('Title (e.g. Greeting)')}
															bind:value={prompt.title}
															required
														/>
														<Textarea
															className="text-sm px-2 py-1 w-full bg-transparent outline-hidden resize-none"
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
										onChange={() => {
											info.params = { ...info.params, ...params };
										}}
									/>
								</div>
							{/if}

							<div class="flex justify-start {fullHeightTab ? 'my-3' : 'my-4 pb-20'}">
								<button
									class="px-4 py-2 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-lg {loading
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
	</div>
{/if}

<ConfirmDialog
	bind:show={showRenameConfirm}
	title={$i18n.t('Change ID?')}
	onConfirm={() => {
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
