<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher();

	export let state = 'unchecked';
	export let indeterminate = false;
	export let disabled = false;

	let _state = 'unchecked';

	$: _state = state;
</script>

<!-- 透明背景だと置かれた面と同化するので、箱は raised + line 縁で固定。
     checked は primary トークン(light=黒箱白チェック / dark=白箱黒チェック) -->
<button
	class=" outline -outline-offset-1 outline-[1.5px] {state !== 'unchecked'
		? 'bg-primary outline-primary text-primary-content'
		: 'bg-surface-raised outline-line hover:outline-content-subtle'} transition-all rounded-sm inline-block w-3.5 h-3.5 relative {disabled
		? 'opacity-50 cursor-not-allowed'
		: ''}"
	on:click={() => {
		if (disabled) return;

		if (_state === 'unchecked') {
			_state = 'checked';
			dispatch('change', _state);
		} else if (_state === 'checked') {
			_state = 'unchecked';
			if (!indeterminate) {
				dispatch('change', _state);
			}
		} else if (indeterminate) {
			_state = 'checked';
			dispatch('change', _state);
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
