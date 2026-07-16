<script lang="ts">
	import { getContext } from 'svelte';
	import Modal from '../common/Modal.svelte';

	const i18n = getContext('i18n');

	export let show = false;
</script>

<Modal bind:show>
	<div class="text-content">
		<div class=" flex justify-between text-content px-5 pt-4">
			<div class=" text-lg font-medium self-center">{$i18n.t('Keyboard shortcuts')}</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="w-5 h-5"
				>
					<path
						d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
					/>
				</svg>
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full p-5 md:space-x-4 text-content">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<div class="flex flex-col space-y-3 w-full self-start">
					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Open new chat')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Shift
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								O
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Focus chat input')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Shift
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Esc
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Copy last code block')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Shift
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								;
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Copy last response')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Shift
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								C
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Generate prompt pair')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Shift
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Enter
							</div>
						</div>
					</div>
				</div>

				<div class="flex flex-col space-y-3 w-full self-start">
					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Toggle settings')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								.
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Toggle sidebar')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Shift
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								S
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Delete chat')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Shift
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								⌫/Delete
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">{$i18n.t('Show shortcuts')}</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								Ctrl/⌘
							</div>

							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								/
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>

		<div class=" flex justify-between text-content px-5">
			<div class=" text-lg font-medium self-center">{$i18n.t('Input commands')}</div>
		</div>

		<div class="flex flex-col md:flex-row w-full p-5 md:space-x-4 text-content">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<div class="flex flex-col space-y-3 w-full self-start">
					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">
							{$i18n.t('Attach file from knowledge')}
						</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								#
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">
							{$i18n.t('Add custom prompt')}
						</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								/
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">
							{$i18n.t('Talk to model')}
						</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								@
							</div>
						</div>
					</div>

					<div class="w-full flex justify-between items-center">
						<div class=" text-sm">
							{$i18n.t('Accept autocomplete generation / Jump to prompt variable')}
						</div>

						<div class="flex space-x-1 text-xs">
							<div
								class=" h-fit py-1 px-2 flex items-center justify-center rounded-sm border border-black/10 capitalize text-content dark:border-white/10"
							>
								TAB
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>
	</div>
</Modal>
