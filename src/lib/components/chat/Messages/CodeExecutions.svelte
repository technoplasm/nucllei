<script lang="ts">
	import CodeExecutionModal from './CodeExecutionModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import type { CodeExecution } from '$lib/types/chat';

	let { codeExecutions = [] }: { codeExecutions?: CodeExecution[] } = $props();

	let selectedCodeExecutionId: string | null = $state(null);
	let showCodeExecutionModal = $state(false);

	// codeExecutions はストリーミング中に丸ごと差し替わるので、id から都度引き直す
	const selectedCodeExecution = $derived(
		codeExecutions.find((execution) => execution.id === selectedCodeExecutionId) ?? null
	);
</script>

<CodeExecutionModal bind:show={showCodeExecutionModal} codeExecution={selectedCodeExecution} />

{#if codeExecutions.length > 0}
	<div class="mt-1 mb-2 w-full flex gap-1 items-center flex-wrap">
		{#each codeExecutions as execution (execution.id)}
			<div class="flex gap-1 text-xs font-semibold">
				<button
					class="flex text-content py-1 px-1 bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-xl max-w-96"
					onclick={() => {
						selectedCodeExecutionId = execution.id;
						showCodeExecutionModal = true;
					}}
				>
					<div
						class="bg-white dark:bg-gray-700 rounded-full size-4 flex items-center justify-center"
					>
						{#if execution?.result}
							{#if execution.result?.error}
								<span class="i-lucide-x size-3.5 block"></span>
							{:else if execution.result?.output}
								<span class="i-lucide-check size-3 block"></span>
							{:else}
								<span class="i-lucide-ellipsis size-4 block"></span>
							{/if}
						{:else}
							<Spinner className="size-4" />
						{/if}
					</div>
					<div
						class="flex-1 mx-2 line-clamp-1 code-execution-name {execution?.result ? '' : 'pulse'}"
					>
						{execution.name}
					</div>
				</button>
			</div>
		{/each}
	</div>
{/if}

<style>
	@keyframes pulse {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.6;
		}
	}

	.pulse {
		opacity: 1;
		animation: pulse 1.5s ease;
	}
</style>
