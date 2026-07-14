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
			controls: true,
			file_upload: true,
			delete: true,
			edit: true,
			share: true,
			clone: false,
			multiple_models: true
		},
		features: {
			image_generation: true
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
		},
		features: {
			...defaultPermissions.features,
			...pick(permissions?.features, Object.keys(defaultPermissions.features))
		}
	};
</script>

<div>
	<div>
		<div class=" mb-2 text-sm font-medium">{$i18n.t('Workspace Permissions')}</div>

		<div class=" ">
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

	<hr class=" border-line my-2" />

	<div>
		<div class=" mb-2 text-sm font-medium">{$i18n.t('Chat Permissions')}</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Allow File Upload')}
			</div>

			<Switch bind:state={permissions.chat.file_upload} />
		</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Allow Chat Controls')}
			</div>

			<Switch bind:state={permissions.chat.controls} />
		</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Allow Chat Delete')}
			</div>

			<Switch bind:state={permissions.chat.delete} />
		</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Allow Chat Edit')}
			</div>

			<Switch bind:state={permissions.chat.edit} />
		</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Allow Chat Share')}
			</div>

			<Switch bind:state={permissions.chat.share} />
		</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Allow Shared Chat Clone')}
			</div>

			<Switch bind:state={permissions.chat.clone} />
		</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Allow Multiple Models in Chat')}
			</div>

			<Switch bind:state={permissions.chat.multiple_models} />
		</div>
	</div>

	<hr class=" border-line my-2" />

	<div>
		<div class=" mb-2 text-sm font-medium">{$i18n.t('Features Permissions')}</div>

		<div class="  flex w-full justify-between my-2 pr-2">
			<div class=" self-center text-xs font-medium">
				{$i18n.t('Image Generation')}
			</div>

			<Switch bind:state={permissions.features.image_generation} />
		</div>
	</div>
</div>
