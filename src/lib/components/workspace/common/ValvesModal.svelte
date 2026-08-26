<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext } from 'svelte';

	import {
		getFunctionValvesById,
		getFunctionValvesSpecById,
		updateFunctionValvesById
	} from '$lib/apis/functions';
	import { getToolValvesById, getToolValvesSpecById, updateToolValvesById } from '$lib/apis/tools';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Valves from '$lib/components/common/Valves.svelte';

	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		type = 'tool',
		id = null,
		onSave = () => {}
	}: {
		show?: boolean;
		type?: 'tool' | 'function';
		id?: string | null;
		onSave?: () => void;
	} = $props();

	let saving = $state(false);
	let loading = $state(false);

	let valvesSpec = $state<any>(null);
	let valves = $state<Record<string, any>>({});

	const submitHandler = async () => {
		const targetId = id;
		if (!targetId) return;
		saving = true;

		if (valvesSpec) {
			// Convert string to array
			for (const property in valvesSpec.properties) {
				if (valvesSpec.properties[property]?.type === 'array') {
					valves[property] = (valves[property] ?? '').split(',').map((v: string) => v.trim());
				}
			}

			let res = null;

			if (type === 'tool') {
				res = await updateToolValvesById(targetId, valves).catch((error) => {
					toast.error(`${error}`);
				});
			} else if (type === 'function') {
				res = await updateFunctionValvesById(targetId, valves).catch((error) => {
					toast.error(`${error}`);
				});
			}

			if (res) {
				toast.success($i18n.t('Values updated successfully'));
				onSave();
			}
		}

		saving = false;
	};

	const initHandler = async () => {
		const targetId = id;
		if (!targetId) return;
		loading = true;
		valves = {};
		valvesSpec = null;

		if (type === 'tool') {
			valves = await getToolValvesById(targetId);
			valvesSpec = await getToolValvesSpecById(targetId);
		} else if (type === 'function') {
			valves = await getFunctionValvesById(targetId);
			valvesSpec = await getFunctionValvesSpecById(targetId);
		}

		if (!valves) {
			valves = {};
		}

		if (valvesSpec) {
			// Convert array to string
			for (const property in valvesSpec.properties) {
				if (valvesSpec.properties[property]?.type === 'array') {
					valves[property] = (valves[property] ?? []).join(',');
				}
			}
		}

		loading = false;
	};

	// 開くたびに対象の valves を読み直す(show の変化検知でガード)
	let lastShow = false;
	$effect(() => {
		if (show === lastShow) return;
		lastShow = show;
		if (show) {
			initHandler();
		}
	});
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between text-content px-5 pt-4 pb-2">
			<div class=" text-lg font-medium self-center">{$i18n.t('Values')}</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				onclick={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-5 pb-4 md:space-x-4 text-content">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<form
					class="flex flex-col w-full"
					onsubmit={(e) => {
						e.preventDefault();
						submitHandler();
					}}
				>
					<div class="px-1">
						{#if !loading}
							<Valves {valvesSpec} bind:valves />
						{:else}
							<Spinner className="size-5" />
						{/if}
					</div>

					<div class="flex justify-end pt-3 text-sm font-medium">
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full {saving
								? ' cursor-not-allowed'
								: ''}"
							type="submit"
							disabled={saving}
						>
							{$i18n.t('Save')}

							{#if saving}
								<span class="i-lucide-loader-circle size-4 block animate-spin ml-2 self-center"
								></span>
							{/if}
						</button>
					</div>
				</form>
			</div>
		</div>
	</div>
</Modal>
