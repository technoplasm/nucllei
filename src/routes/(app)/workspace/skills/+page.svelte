<script lang="ts">
	// air-gapped: openwebui.com/community 連携(share・clone・import/export JSON)は
	// Prompts からコピーする際に意図的に落とした(issue #35)。機能は一覧/検索/作成/編集/削除のみ。
	// llens(#47 A1): By 表示・pencil・… メニューを撤去。クリックで編集、削除は trash、
	// visibility(access_control)は一覧のアイコンから直接編集する。
	// llens(#66): editor は全 skill を閲覧できるが、ACL 編集は admin のみ・削除は自分の
	// skill のみ(サーバー側ゲートのミラー)。
	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME, skills as _skills, user } from '$lib/stores';

	import { deleteSkillById, getSkills, getSkillList, updateSkillById } from '$lib/apis/skills';

	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';

	const i18n = getContext('i18n');

	let loaded = $state(false);
	let query = $state('');
	let skills = $state<any[]>([]);

	let showDeleteConfirm = $state(false);
	let deleteSkill = $state<any>(null);

	// visibility(access_control)の一覧内編集: アイコン → AccessControlModal、閉じたら保存
	let showAccessControlModal = $state(false);
	let acItem = $state<any>(null);
	let acValue = $state<any>(null);

	const filteredItems = $derived(skills.filter((s) => query === '' || s.id.includes(query)));

	const openAccessControl = (skill: any) => {
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

		const res = await updateSkillById({
			...item,
			access_control: value
		}).catch(() => null);

		if (res) {
			await init();
		}
	};

	$effect(() => {
		if (!showAccessControlModal && acItem) {
			commitAccessControl();
		}
	});

	const deleteHandler = async (skill: any) => {
		await deleteSkillById(skill.id);
		await init();
	};

	const init = async () => {
		skills = await getSkillList();
		await _skills.set(await getSkills());
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
		<div class=" text-sm text-content-muted">
			{$i18n.t('This will delete')} <span class=" font-semibold">{deleteSkill.id}</span>.
		</div>
	</DeleteConfirmDialog>

	<!-- ヘッダ様式: 見出しで 1 行(count なし)/ 2 行目に Create pill + 検索 -->
	<div class="flex flex-col gap-1 mt-1.5">
		<div class="flex items-center text-xl font-medium px-0.5">
			{$i18n.t('Skills')}
		</div>

		<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
			<a
				class="shrink-0 whitespace-nowrap inline-flex items-center py-0.5 pl-2 pr-3 rounded-full bg-primary hover:bg-primary-hover text-primary-content font-medium text-sm"
				href="/workspace/skills/create"
			>
				<span class="i-lucide-plus size-3.5 block mr-1"></span>
				{$i18n.t('Create Skill')}
			</a>

			<div class="flex items-center px-2 bg-surface-raised rounded-xs">
				<span class="i-lucide-search size-3.5 block shrink-0"></span>
				<input
					class="w-full sm:w-48 text-sm px-2 py-1 bg-transparent outline-hidden"
					bind:value={query}
					placeholder={$i18n.t('Search Skills')}
				/>
			</div>
		</div>
	</div>

	<div class="mt-6 gap-3 grid lg:grid-cols-2 xl:grid-cols-3">
		{#each filteredItems as skill (skill.id)}
			<div
				class="relative flex items-center gap-0.5 px-4 py-3 bg-surface-raised [&:hover:not(:has(button:hover))]:bg-surface-hover rounded-xl"
			>
				<a
					class="flex-1 min-w-0 cursor-pointer after:absolute after:inset-0 after:content-['']"
					href={`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`}
				>
					<div class="font-semibold line-clamp-1 capitalize">{skill.name}</div>
					<div class="flex gap-1.5 text-xs">
						<span class="text-content-muted font-medium shrink-0">/{skill.id}</span>
						<span class="line-clamp-1">{skill.description}</span>
					</div>
				</a>

				<Tooltip
					className="relative flex"
					content={(skill.access_control ?? null) === null ? $i18n.t('Public') : $i18n.t('Private')}
				>
					{#if $user?.role === 'admin'}
						<button
							class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
							type="button"
							aria-label={(skill.access_control ?? null) === null
								? $i18n.t('Public')
								: $i18n.t('Private')}
							onclick={() => openAccessControl(skill)}
						>
							{#if (skill.access_control ?? null) === null}
								<span class="i-lucide-globe size-4 block"></span>
							{:else}
								<span class="i-lucide-lock size-4 block"></span>
							{/if}
						</button>
					{:else}
						<!-- 公開範囲の変更は admin のみ — 表示だけ -->
						<div class="px-2 py-2 text-content-subtle">
							{#if (skill.access_control ?? null) === null}
								<span class="i-lucide-globe size-4 block"></span>
							{:else}
								<span class="i-lucide-lock size-4 block"></span>
							{/if}
						</div>
					{/if}
				</Tooltip>

				{#if $user?.role === 'admin' || skill.user_id === $user?.id}
					<Tooltip className="relative flex" content={$i18n.t('Delete')}>
						<button
							class="px-2 py-2 text-content bg-transparent hover:bg-surface-hover rounded-xl"
							type="button"
							aria-label={$i18n.t('Delete')}
							onclick={() => {
								deleteSkill = skill;
								showDeleteConfirm = true;
							}}
						>
							<span class="i-lucide-trash-2 size-4 block"></span>
						</button>
					</Tooltip>
				{/if}
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
