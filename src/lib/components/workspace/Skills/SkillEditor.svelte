<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';

	import Textarea from '$lib/components/common/Textarea.svelte';
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

	let title = '';
	let command = '';
	let description = '';
	let content = '';

	// tool_ids は admin のみ編集可(バックエンドが 401 を返すゲートと整合)。
	// step2b まで runtime では未消費 — この段階では preset 側の紐づけ情報の保存のみ。
	let tools = [];
	let _tools = {};
	let toolIds = [];

	let accessControl = {};

	let showAccessControlModal = false;

	$: if (!edit) {
		command = title !== '' ? `${title.replace(/\s+/g, '-').toLowerCase()}` : '';
	}

	const submitHandler = async () => {
		loading = true;

		if (validateCommandString(command)) {
			await onSubmit({
				title,
				command,
				// description が空ならモデルには公開されない(バックエンド仕様)。
				description: description,
				content,
				tool_ids: toolIds,
				access_control: accessControl
			});
		} else {
			toast.error(
				$i18n.t('Only alphanumeric characters and hyphens are allowed in the command string.')
			);
		}

		loading = false;
	};

	const validateCommandString = (inputString) => {
		// 英数字とハイフンのみ許可(PromptEditor 踏襲)
		const regex = /^[a-zA-Z0-9-]+$/;

		return regex.test(inputString);
	};

	onMount(async () => {
		if (skill) {
			title = skill.title;
			await tick();

			command = skill.command.at(0) === '/' ? skill.command.slice(1) : skill.command;
			description = skill.description ?? '';
			content = skill.content;
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

<AccessControlModal
	bind:show={showAccessControlModal}
	bind:accessControl
	accessRoles={['read', 'write']}
	allowPublic={$user?.permissions?.sharing?.public_skills || $user?.role === 'admin'}
/>

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
						COMMAND: command
					}
				)}`}
				placement="bottom-start"
			>
				<div class="flex flex-col w-full">
					<div class="flex items-center">
						<input
							class="text-2xl font-semibold w-full bg-transparent outline-hidden"
							placeholder={$i18n.t('Title')}
							bind:value={title}
							required
						/>

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
					</div>

					<div class="flex gap-0.5 items-center text-xs text-gray-500">
						<div class="">/</div>
						<input
							class=" w-full bg-transparent outline-hidden"
							placeholder={$i18n.t('Command')}
							bind:value={command}
							required
							disabled={edit}
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
				<div>
					<Textarea
						className="text-sm w-full bg-transparent outline-hidden overflow-y-hidden resize-none"
						placeholder={$i18n.t('Write a summary in 50 words that summarizes [topic or keyword].')}
						bind:value={content}
						rows={6}
						required
					/>
				</div>

				<div class="text-xs text-gray-400 dark:text-gray-500">
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

		<div class="my-4 flex justify-end pb-20">
			<button
				class=" text-sm w-full lg:w-fit px-4 py-2 transition rounded-lg {loading
					? ' cursor-not-allowed bg-black hover:bg-gray-900 text-white dark:bg-white dark:hover:bg-gray-100 dark:text-black'
					: 'bg-black hover:bg-gray-900 text-white dark:bg-white dark:hover:bg-gray-100 dark:text-black'} flex w-full justify-center"
				type="submit"
				disabled={loading}
			>
				<div class=" self-center font-medium">{$i18n.t('Save & Create')}</div>

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
