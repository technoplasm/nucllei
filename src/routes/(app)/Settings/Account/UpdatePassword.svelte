<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { updateUserPassword } from '$lib/apis/auths';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n');

	let currentPassword = $state('');
	let newPassword = $state('');
	let newPasswordConfirm = $state('');

	const updatePasswordHandler = async () => {
		if (newPassword === newPasswordConfirm) {
			const res = await updateUserPassword(currentPassword, newPassword).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (res) {
				toast.success($i18n.t('Successfully updated.'));
			}

			currentPassword = '';
			newPassword = '';
			newPasswordConfirm = '';
		} else {
			toast.error(
				$i18n.t("The passwords you entered don't quite match. Please double-check and try again.")
			);
			newPassword = '';
			newPasswordConfirm = '';
		}
	};
</script>

<form
	class="flex flex-col text-sm"
	onsubmit={(e) => {
		e.preventDefault();
		updatePasswordHandler();
	}}
>
	<div class="flex justify-between items-center text-sm">
		<div class="  font-medium">{$i18n.t('Change Password')}</div>
	</div>

	<div class=" py-2.5 space-y-1.5">
		<label class="flex flex-col w-full">
			<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Current Password')}</div>

			<SensitiveInput
				outerClassName="flex w-full px-2 bg-surface-muted rounded-xs"
				inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
				bind:value={currentPassword}
				placeholder={$i18n.t('Enter your current password')}
			/>
		</label>

		<label class="flex flex-col w-full">
			<div class=" mb-1 text-xs text-content-muted">{$i18n.t('New Password')}</div>

			<SensitiveInput
				outerClassName="flex w-full px-2 bg-surface-muted rounded-xs"
				inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
				bind:value={newPassword}
				placeholder={$i18n.t('Enter your new password')}
			/>
		</label>

		<label class="flex flex-col w-full">
			<div class=" mb-1 text-xs text-content-muted">{$i18n.t('Confirm Password')}</div>

			<SensitiveInput
				outerClassName="flex w-full px-2 bg-surface-muted rounded-xs"
				inputClassName="w-full text-sm py-1 placeholder:text-content-subtle bg-transparent outline-hidden"
				bind:value={newPasswordConfirm}
				placeholder={$i18n.t('Confirm your new password')}
			/>
		</label>
	</div>

	<div class="mt-3 flex justify-end">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-primary hover:bg-primary-hover text-primary-content rounded-full"
		>
			{$i18n.t('Update password')}
		</button>
	</div>
</form>
