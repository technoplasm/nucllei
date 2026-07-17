<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import AccessControl from './AccessControl.svelte';

	export let show = false;
	export let accessControl = {};
	export let allowPublic = true;

	// llens(#69): file 用 variant('default' | 'file')。AccessControl へパススルー。
	export let variant = 'default';

	export let onChange = () => {};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-3 pb-1">
			<div class=" text-lg font-medium self-center font-primary">
				{$i18n.t('Access Control')}
			</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="w-full px-5 pb-4 dark:text-white">
			<AccessControl bind:accessControl {onChange} {allowPublic} {variant} />
		</div>
	</div>
</Modal>
