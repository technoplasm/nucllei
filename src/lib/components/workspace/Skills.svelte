<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME, skills as _skills, user } from '$lib/stores';

	import { deleteSkillById, getSkills, getSkillList, updateSkillById } from '$lib/apis/skills';

	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import GlobeAlt from '../icons/GlobeAlt.svelte';
	import LockClosed from '../icons/LockClosed.svelte';
	import AccessControlModal from './common/AccessControlModal.svelte';

	// air-gapped: openwebui.com/community 連携(share・clone・import/export JSON)は
	// Prompts からコピーする際に意図的に落とした(issue #35)。機能は一覧/検索/作成/編集/削除のみ。
	// llens(#47 A1): By 表示・pencil・… メニューを撤去。クリックで編集、削除は trash、
	// visibility(access_control)は一覧のアイコンから直接編集する。
	// llens(#66): editor は全 skill を閲覧できるが、ACL 編集は admin のみ・削除は自分の
	// skill のみ(サーバー側ゲートのミラー)。
	const i18n = getContext('i18n');
	let loaded = false;

	let query = '';

	let skills = [];

	let showDeleteConfirm = false;
	let deleteSkill = null;

	// visibility(access_control)の一覧内編集: アイコン → AccessControlModal、閉じたら保存
	let showAccessControlModal = false;
	let acItem = null;
	let acValue = null;

	let filteredItems = [];
	$: filteredItems = skills.filter((s) => query === '' || s.id.includes(query));

	const openAccessControl = (skill) => {
		acItem = skill;
		acValue = skill?.access_control ?? null;
		showAccessControlModal = true;
	};

	// skill の list は content 込み(SkillUserResponse)なのでそのまま round-trip できる。
	const commitAccessControl = async () => {
		const item = acItem;
		const value = acValue;
		acItem = null;
		if (!item) return;

		const res = await updateSkillById(localStorage.token, {
			...item,
			access_control: value
		}).catch(() => null);

		if (res) {
			await init();
		}
	};

	$: if (!showAccessControlModal && acItem) {
		commitAccessControl();
	}

	const deleteHandler = async (skill) => {
		const id = skill.id;
		await deleteSkillById(localStorage.token, id);
		await init();
	};

	const init = async () => {
		skills = await getSkillList(localStorage.token);
		await _skills.set(await getSkills(localStorage.token));
	};

	onMount(async () => {
		await init();
		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Skills')} | {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete skill?')}
		on:confirm={() => {
			deleteHandler(deleteSkill);
		}}
	>
		<div class=" text-sm text-gray-500">
			{$i18n.t('This will delete')} <span class="  font-semibold">{deleteSkill.id}</span>.
		</div>
	</DeleteConfirmDialog>

	<div class="flex flex-col gap-1 my-1.5">
		<div class="flex justify-between items-center">
			<div class="flex md:self-center text-xl font-medium px-0.5 items-center">
				{$i18n.t('Skills')}
				<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-50 dark:bg-gray-850" />
				<span class="text-lg font-medium text-gray-500 dark:text-gray-300"
					>{filteredItems.length}</span
				>
			</div>
		</div>

		<div class=" flex w-full space-x-2">
			<div class="flex flex-1">
				<div class=" self-center ml-1 mr-3">
					<Search className="size-3.5" />
				</div>
				<input
					class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
					bind:value={query}
					placeholder={$i18n.t('Search Skills')}
				/>
			</div>

			<div>
				<a
					class=" px-2 py-2 rounded-xl hover:bg-gray-700/10 dark:hover:bg-gray-100/10 dark:text-gray-300 dark:hover:text-white transition font-medium text-sm flex items-center space-x-1"
					href="/workspace/skills/create"
				>
					<Plus className="size-3.5" />
				</a>
			</div>
		</div>
	</div>

	<div class="mb-5 gap-2 grid lg:grid-cols-2 xl:grid-cols-3">
		{#each filteredItems as skill}
			<div
				class=" flex space-x-4 cursor-pointer w-full px-3 py-2 dark:hover:bg-white/5 hover:bg-black/5 rounded-xl transition"
			>
				<a
					class=" flex flex-1 min-w-0 cursor-pointer"
					href={`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`}
				>
					<div class=" flex-1 self-center min-w-0">
						<div class=" flex items-center gap-2 min-w-0">
							<div class=" font-semibold line-clamp-1 capitalize">{skill.name}</div>
							<div class=" text-xs text-gray-500 line-clamp-1 shrink-0">/{skill.id}</div>
						</div>
						<div class="px-0.5 text-xs overflow-hidden text-ellipsis line-clamp-1 mt-0.5">
							{skill.description}
						</div>
					</div>
				</a>

				<div class="flex flex-row gap-0.5 self-center">
					<Tooltip
						content={(skill.access_control ?? null) === null
							? $i18n.t('Public')
							: $i18n.t('Private')}
					>
						{#if $user?.role === 'admin'}
							<button
								class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
								type="button"
								on:click={() => openAccessControl(skill)}
							>
								{#if (skill.access_control ?? null) === null}
									<GlobeAlt className="size-4" />
								{:else}
									<LockClosed className="size-4" />
								{/if}
							</button>
						{:else}
							<!-- 公開範囲の変更は admin のみ — 表示だけ -->
							<div class="self-center w-fit text-sm px-2 py-2 text-gray-400 dark:text-gray-600">
								{#if (skill.access_control ?? null) === null}
									<GlobeAlt className="size-4" />
								{:else}
									<LockClosed className="size-4" />
								{/if}
							</div>
						{/if}
					</Tooltip>

					{#if $user?.role === 'admin' || skill.user_id === $user?.id}
						<Tooltip content={$i18n.t('Delete')}>
							<button
								class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
								type="button"
								on:click={() => {
									deleteSkill = skill;
									showDeleteConfirm = true;
								}}
							>
								<GarbageBin />
							</button>
						</Tooltip>
					{/if}
				</div>
			</div>
		{/each}
	</div>

	<AccessControlModal
		bind:show={showAccessControlModal}
		bind:accessControl={acValue}
		allowPublic={true}
	/>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
