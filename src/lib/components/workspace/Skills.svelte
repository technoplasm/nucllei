<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME, skills as _skills, user } from '$lib/stores';

	import { deleteSkillById, getSkills, getSkillList } from '$lib/apis/skills';

	import SkillMenu from './Skills/SkillMenu.svelte';
	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import { capitalizeFirstLetter } from '$lib/utils';

	// air-gapped: openwebui.com/community 連携(share・clone・import/export JSON)は
	// Prompts からコピーする際に意図的に落とした(issue #35)。機能は一覧/検索/作成/編集/削除のみ。
	const i18n = getContext('i18n');
	let loaded = false;

	let query = '';

	let skills = [];

	let showDeleteConfirm = false;
	let deleteSkill = null;

	let filteredItems = [];
	$: filteredItems = skills.filter((s) => query === '' || s.id.includes(query));

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
				<div class=" flex flex-1 space-x-4 cursor-pointer w-full">
					<a href={`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`}>
						<div class=" flex-1 flex items-center gap-2 self-center">
							<div class=" font-semibold line-clamp-1 capitalize">{skill.name}</div>
							<div class=" text-xs overflow-hidden text-ellipsis line-clamp-1">
								/{skill.id}
							</div>
						</div>

						<div class=" text-xs px-0.5">
							<Tooltip
								content={skill?.user?.email ?? $i18n.t('Deleted User')}
								className="flex shrink-0"
								placement="top-start"
							>
								<div class="shrink-0 text-gray-500">
									{$i18n.t('By {{name}}', {
										name: capitalizeFirstLetter(
											skill?.user?.name ?? skill?.user?.email ?? $i18n.t('Deleted User')
										)
									})}
								</div>
							</Tooltip>
						</div>
					</a>
				</div>
				<div class="flex flex-row gap-0.5 self-center">
					<a
						class="self-center w-fit text-sm px-2 py-2 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
						type="button"
						href={`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="1.5"
							stroke="currentColor"
							class="w-4 h-4"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125"
							/>
						</svg>
					</a>

					<SkillMenu
						deleteHandler={async () => {
							deleteSkill = skill;
							showDeleteConfirm = true;
						}}
						onClose={() => {}}
					>
						<button
							class="self-center w-fit text-sm p-1.5 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
							type="button"
						>
							<EllipsisHorizontal className="size-5" />
						</button>
					</SkillMenu>
				</div>
			</div>
		{/each}
	</div>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner />
	</div>
{/if}
