<script lang="ts">
	import { getContext } from 'svelte';

	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	// llens(#66): 権限3層化 — workspace は skills(= skill editor ロール)のみ。
	// 旧 workspace.models/tools/knowledge・sharing.*・chat.stt/tts/call は廃止。
	const defaultPermissions = {
		workspace: {
			skills: false
		},
		chat: {
			file_upload: true,
			delete: true,
			edit: true,
			share: true,
			clone: false
		}
	};

	let { permissions = $bindable({}) }: { permissions?: any } = $props();

	function pick(obj: any, keys: string[]) {
		return Object.fromEntries(Object.entries(obj ?? {}).filter(([key]) => keys.includes(key)));
	}

	// 廃止済みキーが保存値に残っていても拾わない(defaults の形に射影する)
	permissions = {
		workspace: { skills: permissions?.workspace?.skills ?? false },
		chat: {
			...defaultPermissions.chat,
			...pick(permissions?.chat, Object.keys(defaultPermissions.chat))
		}
	};
</script>

<!-- 行を全幅 justify-between で並べるとラベルとトグルの目線が離れるので、
 ~200px セルの auto-fill grid に畳む(実測: インライン 836px → 3 列 / モーダル 464px → 2 列。
     コンテナ幅で列数が決まるので同一コンポーネントのまま両置き場に効く) -->
<div>
	<div>
		<div class="mb-1 text-sm font-medium text-content-muted">{$i18n.t('Chat Permissions')}</div>

		<div class="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-x-6">
			<div class=" flex w-full justify-between my-2 pr-2">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Allow File Upload')}
				</div>

				<Switch bind:state={permissions.chat.file_upload} />
			</div>

			<div class=" flex w-full justify-between my-2 pr-2">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Allow Chat Delete')}
				</div>

				<Switch bind:state={permissions.chat.delete} />
			</div>

			<div class=" flex w-full justify-between my-2 pr-2">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Allow Chat Edit')}
				</div>

				<Switch bind:state={permissions.chat.edit} />
			</div>

			<div class=" flex w-full justify-between my-2 pr-2">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Allow Chat Share')}
				</div>

				<Switch bind:state={permissions.chat.share} />
			</div>

			<div class=" flex w-full justify-between my-2 pr-2">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Allow Shared Chat Clone')}
				</div>

				<Switch bind:state={permissions.chat.clone} />
			</div>
		</div>
	</div>

	<!-- チャット基本操作の外側にある能力(skill editor ロール)を Features に集約 -->
	<div class="mt-2">
		<div class="mb-1 text-sm font-medium text-content-muted">{$i18n.t('Features')}</div>

		<div class="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-x-6">
			<Tooltip
				className=" flex w-full justify-between my-2 pr-2"
				content={$i18n.t(
					'Skill editors can view all skills and create private ones; they can edit only their own. Sharing is admin-only.'
				)}
				placement="top-start"
			>
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Skill Editor')}
				</div>
				<Switch bind:state={permissions.workspace.skills} />
			</Tooltip>
		</div>
	</div>
</div>
