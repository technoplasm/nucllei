<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';

	import Textarea from '$lib/components/common/Textarea.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import { toast } from 'svelte-sonner';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import { user } from '$lib/stores';
	import { getTools } from '$lib/apis/tools';

	export let onSubmit: Function;
	export let edit = false;
	export let skill = null;

	const i18n = getContext('i18n');

	let loading = false;

	// llens(#43): 可読キーは id 一本(旧 command)、表示名は name(旧 title)。
	let name = '';
	let id = '';
	let description = '';
	let content = '';
	// 本文は CodeEditor で編集。value は _content にミラーし、submit 時に content へ確定。
	let _content = '';

	// llens(#43 Phase2b): id リネームは「旧 id 削除 → 新 id 作成」で差し替える(PK 不変)。
	// 編集開始時の id を控え、変更があれば確認ダイアログを出す。
	let originalId = '';
	let showRenameConfirm = false;

	// tool_ids は admin のみ編集可(バックエンドが 401 を返すゲートと整合)。
	// step2b まで runtime では未消費 — この段階では preset 側の紐づけ情報の保存のみ。
	let tools = [];
	let _tools = {};
	let toolIds = [];

	let accessControl = {};

	let showAccessControlModal = false;

	// llens(#66): 編集できるのは自分の skill だけ(admin は全部)。他人の skill は閲覧のみ。
	// ACL(公開範囲)を触れるのは admin のみ — editor の保存では server 側でも温存される。
	$: canEdit = !edit || $user?.role === 'admin' || skill?.user_id === $user?.id;

	$: if (!edit) {
		id = name !== '' ? `${name.replace(/\s+/g, '-').toLowerCase()}` : '';
	}

	const submitHandler = async () => {
		content = _content;

		if (!validateCommandString(id)) {
			toast.error(
				$i18n.t('Only alphanumeric characters and hyphens are allowed in the command string.')
			);
			return;
		}

		if (content.trim() === '') {
			toast.error($i18n.t('Skill Content is required'));
			return;
		}

		// 編集で id が変わったら、割り当て・valves がちぎれる旨を確認してから差し替える。
		if (edit && id !== originalId) {
			showRenameConfirm = true;
			return;
		}

		await doSubmit();
	};

	const doSubmit = async () => {
		loading = true;
		await onSubmit({
			name,
			id,
			// description が空ならモデルには公開されない(バックエンド仕様)。
			description: description,
			content,
			tool_ids: toolIds,
			access_control: accessControl
		});
		loading = false;
	};

	const validateCommandString = (inputString) => {
		// 英数字とハイフンのみ許可(PromptEditor 踏襲)
		const regex = /^[a-zA-Z0-9-]+$/;

		return regex.test(inputString);
	};

	onMount(async () => {
		if (skill) {
			name = skill.name;
			await tick();

			id = skill.id.at(0) === '/' ? skill.id.slice(1) : skill.id;
			originalId = id;
			description = skill.description ?? '';
			content = skill.content;
			_content = content;
			toolIds = skill?.tool_ids ?? [];

			accessControl = skill?.access_control ?? null;
		}

		// tool 選択肢は admin のときのみ取得・描画(非 admin には出さない)
		if ($user?.role === 'admin') {
			tools = (await getTools(localStorage.token).catch(() => [])) ?? [];
			_tools = tools.reduce((acc, tool) => {
				acc[tool.id] = {
					...tool,
					selected: toolIds.includes(tool.id)
				};
				return acc;
			}, {});
		}
	});
</script>

<AccessControlModal bind:show={showAccessControlModal} bind:accessControl allowPublic={true} />

<div class="w-full max-h-full flex justify-center">
	<form
		class="flex flex-col w-full mb-10"
		on:submit|preventDefault={() => {
			submitHandler();
		}}
	>
		<div class="my-2">
			<Tooltip
				content={`${$i18n.t('Only alphanumeric characters and hyphens are allowed')} - ${$i18n.t(
					'Activate this command by typing "/{{COMMAND}}" to chat input.',
					{
						COMMAND: id
					}
				)}`}
				placement="bottom-start"
			>
				<div class="flex flex-col w-full">
					<div class="flex items-center">
						<input
							class="text-2xl font-semibold w-full bg-transparent outline-hidden"
							placeholder={$i18n.t('Title')}
							bind:value={name}
							required
						/>

						{#if $user?.role === 'admin'}
							<div class="self-center shrink-0">
								<button
									class="bg-gray-50 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center"
									type="button"
									on:click={() => {
										showAccessControlModal = true;
									}}
								>
									<LockClosed strokeWidth="2.5" className="size-3.5" />

									<div class="text-sm font-medium shrink-0">
										{$i18n.t('Access')}
									</div>
								</button>
							</div>
						{/if}
					</div>

					<div class="flex gap-0.5 items-center text-xs text-gray-500">
						<div class="">/</div>
						<input
							class=" w-full bg-transparent outline-hidden"
							placeholder={$i18n.t('Command')}
							bind:value={id}
							required
						/>
					</div>
				</div>
			</Tooltip>
		</div>

		<div class="my-2">
			<div class="flex w-full justify-between">
				<div class=" self-center text-sm font-semibold">{$i18n.t('Description (for model)')}</div>
			</div>

			<div class="mt-2">
				<Textarea
					className="text-sm w-full bg-transparent outline-hidden overflow-y-hidden resize-none"
					placeholder={$i18n.t('モデルが skill を選ぶ説明。空ならモデルには公開されない')}
					bind:value={description}
					rows={2}
				/>
			</div>
		</div>

		<div class="my-2">
			<div class="flex w-full justify-between">
				<div class=" self-center text-sm font-semibold">{$i18n.t('Skill Content')}</div>
			</div>

			<div class="mt-2">
				<div class="h-96 rounded-lg border border-gray-100 dark:border-gray-850 overflow-auto">
					<CodeEditor
						id="skill"
						lang="markdown"
						value={content}
						onChange={(e) => {
							_content = e;
						}}
						onSave={() => {
							submitHandler();
						}}
					/>
				</div>

				<div class="text-xs text-gray-400 dark:text-gray-500 mt-1">
					ⓘ {$i18n.t('Format your variables using brackets like this:')}&nbsp;<span
						class=" text-gray-600 dark:text-gray-300 font-medium"
						>{'{{'}{$i18n.t('variable')}{'}}'}</span
					>.
					{$i18n.t('Make sure to enclose them with')}
					<span class=" text-gray-600 dark:text-gray-300 font-medium">{'{{'}</span>
					{$i18n.t('and')}
					<span class=" text-gray-600 dark:text-gray-300 font-medium">{'}}'}</span>.
				</div>
			</div>
		</div>

		{#if $user?.role === 'admin'}
			<div class="my-2">
				<div class="flex w-full justify-between mb-1">
					<div class=" self-center text-sm font-semibold">{$i18n.t('Tools')}</div>
				</div>

				<div class=" text-xs dark:text-gray-500">
					{$i18n.t('tool 紐づき skill の作成/更新は admin のみ可能です。')}
				</div>

				<div class="flex flex-col">
					{#if tools.length > 0}
						<div class=" flex items-center mt-2 flex-wrap">
							{#each Object.keys(_tools) as tool}
								<div class=" flex items-center gap-2 mr-3">
									<div class="self-center flex items-center">
										<Checkbox
											state={_tools[tool].selected ? 'checked' : 'unchecked'}
											on:change={(e) => {
												_tools[tool].selected = e.detail === 'checked';
												toolIds = Object.keys(_tools).filter((t) => _tools[t].selected);
											}}
										/>
									</div>

									<div class=" py-0.5 text-sm w-full capitalize font-medium">
										{_tools[tool].name}
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			</div>
		{/if}

		<div class="my-4 flex justify-start items-center gap-3 pb-20">
			{#if !canEdit}
				<div class="text-sm text-gray-500">
					{$i18n.t('閲覧のみ(編集できるのは自分のスキルだけです)')}
				</div>
			{/if}
			<button
				class=" text-sm w-fit px-4 py-2 transition rounded-lg bg-black hover:bg-gray-900 text-white dark:bg-white dark:hover:bg-gray-100 dark:text-black {loading ||
				!canEdit
					? ' cursor-not-allowed opacity-50'
					: ''} flex justify-center"
				type="submit"
				disabled={loading || !canEdit}
			>
				<div class=" self-center font-medium">{$i18n.t('Save')}</div>

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
	</form>
</div>

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
				<li>{$i18n.t('Per-item settings such as valves will be reset.')}</li>
			</ul>
		</div>
	</div>
</ConfirmDialog>
