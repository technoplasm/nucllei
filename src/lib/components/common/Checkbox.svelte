<script lang="ts">
	let {
		state = 'unchecked',
		indeterminate = false,
		disabled = false,
		onChange = undefined
	}: {
		state?: 'checked' | 'unchecked';
		indeterminate?: boolean;
		disabled?: boolean;
		onChange?: (state: 'checked' | 'unchecked') => void;
	} = $props();

	// prop の state を初期値に、クリックで上書きされるローカル状態($derived の再代入)
	let _state = $derived(state);
</script>

<!-- 透明背景だと置かれた面と同化するので、箱は raised + line 縁で固定。
     checked は primary トークン(light=黒箱白チェック / dark=白箱黒チェック) -->
<button
	class=" outline -outline-offset-1 outline-[1.5px] {state !== 'unchecked'
		? 'bg-primary outline-primary text-primary-content'
		: 'bg-surface-raised outline-line hover:outline-content-subtle'} transition-all rounded-sm inline-block w-3.5 h-3.5 relative {disabled
		? 'opacity-50 cursor-not-allowed'
		: ''}"
	onclick={() => {
		if (disabled) return;

		if (_state === 'unchecked') {
			_state = 'checked';
			onChange?.(_state);
		} else if (_state === 'checked') {
			_state = 'unchecked';
			if (!indeterminate) {
				onChange?.(_state);
			}
		} else if (indeterminate) {
			_state = 'checked';
			onChange?.(_state);
		}
	}}
	type="button"
	{disabled}
>
	<div class="top-0 left-0 absolute w-full flex justify-center">
		{#if _state === 'checked'}
			<svg
				class="w-3.5 h-3.5"
				aria-hidden="true"
				xmlns="http://www.w3.org/2000/svg"
				fill="none"
				viewBox="0 0 24 24"
			>
				<path
					stroke="currentColor"
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="3"
					d="m5 12 4.7 4.5 9.3-9"
				/>
			</svg>
		{:else if indeterminate}
			<svg
				class="w-3 h-3.5 text-content"
				aria-hidden="true"
				xmlns="http://www.w3.org/2000/svg"
				fill="none"
				viewBox="0 0 24 24"
			>
				<path
					stroke="currentColor"
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="3"
					d="M5 12h14"
				/>
			</svg>
		{/if}
	</div>

	<!-- {checked} -->
</button>
