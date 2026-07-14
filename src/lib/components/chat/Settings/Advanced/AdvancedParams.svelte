<script lang="ts">
	import Switch from '$lib/components/common/Switch.svelte';
	import { createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	export let admin = false;

	export let params: Record<string, any> = {
		// Advanced
		stream_response: null, // Set stream responses for this model individually
		function_calling: null,
		seed: null,
		stop: null,
		temperature: null,
		reasoning_effort: null,
		logit_bias: null,
		frequency_penalty: null,
		presence_penalty: null,
		repeat_last_n: null,
		repeat_penalty: null,
		mirostat: null,
		mirostat_eta: null,
		mirostat_tau: null,
		top_k: null,
		top_p: null,
		min_p: null,
		tfs_z: null,
		num_ctx: null,
		num_batch: null,
		num_keep: null,
		max_tokens: null,
		use_mmap: null,
		use_mlock: null,
		num_thread: null,
		num_gpu: null,
		template: null
	};

	// すべての項目をここで宣言的に定義する。ラベルは実際にシステムへ渡すキー名(item.key)を
	// そのまま表示する。マークアップは下の {#each} 一箇所だけなので、ラベルと値の位置・行
	// レイアウトはテンプレートを一度いじれば全項目に一括で反映される。
	//
	// type:
	//   'cycle'  … states を巡回するボタン（Default トグルなし）
	//   'range'  … Default トグル + スライダー + 数値入力
	//   'input'  … Default トグル + 単一入力（text / number）
	//   'switch' … Default トグル + Switch
	// 型は項目ごとに持つフィールドが違う(range/num/states/inputType…)ため any[] とする。
	const sections: any[] = [
		{
			title: 'Behavior',
			items: [
				{
					key: 'function_calling',
					type: 'cycle',
					states: [
						{ value: null, label: 'Default' },
						{ value: 'native', label: 'Native' }
					]
				},
				{
					key: 'stream_response',
					type: 'cycle',
					states: [
						{ value: null, label: 'Default' },
						{ value: true, label: 'On' },
						{ value: false, label: 'Off' }
					]
				},
				{
					key: 'reasoning_effort',
					type: 'input',
					def: 'medium',
					inputType: 'text',
					placeholder: 'Enter reasoning effort'
				}
			]
		},
		{
			title: 'Sampling',
			items: [
				{
					key: 'temperature',
					type: 'range',
					def: 0.8,
					range: { min: 0, max: 2, step: 0.05 },
					num: { min: 0, max: 2, step: 'any' }
				},
				{
					key: 'top_k',
					type: 'range',
					def: 40,
					range: { min: 0, max: 1000, step: 0.5 },
					num: { min: 0, max: 100, step: 'any' }
				},
				{
					key: 'top_p',
					type: 'range',
					def: 0.9,
					range: { min: 0, max: 1, step: 0.05 },
					num: { min: 0, max: 1, step: 'any' }
				},
				{
					key: 'min_p',
					type: 'range',
					def: 0.0,
					range: { min: 0, max: 1, step: 0.05 },
					num: { min: 0, max: 1, step: 'any' }
				},
				{
					key: 'max_tokens',
					type: 'range',
					def: 128,
					range: { min: -2, max: 131072, step: 1 },
					num: { min: -2, step: 1 }
				},
				{
					key: 'seed',
					type: 'input',
					def: 0,
					inputType: 'number',
					placeholder: 'Enter Seed',
					min: 0
				},
				{
					key: 'stop',
					type: 'input',
					def: '',
					inputType: 'text',
					placeholder: 'Enter stop sequence'
				},
				{
					key: 'frequency_penalty',
					type: 'range',
					def: 1.1,
					range: { min: -2, max: 2, step: 0.05 },
					num: { min: -2, max: 2, step: 'any' }
				},
				{
					key: 'presence_penalty',
					type: 'range',
					def: 0.0,
					range: { min: -2, max: 2, step: 0.05 },
					num: { min: -2, max: 2, step: 'any' }
				},
				{
					key: 'repeat_last_n',
					type: 'range',
					def: 64,
					range: { min: -1, max: 128, step: 1 },
					num: { min: -1, max: 128, step: 1 }
				},
				{
					key: 'repeat_penalty',
					type: 'range',
					def: 1.1,
					range: { min: -2, max: 2, step: 0.05 },
					num: { min: -2, max: 2, step: 'any' }
				},
				{
					key: 'tfs_z',
					type: 'range',
					def: 1,
					range: { min: 0, max: 2, step: 0.05 },
					num: { min: 0, max: 2, step: 'any' }
				},
				{
					key: 'logit_bias',
					type: 'input',
					def: '',
					inputType: 'text',
					placeholder: 'Enter comma-seperated "token:bias_value" pairs (example: 5432:100, 413:-100)'
				}
			]
		},
		{
			title: 'Mirostat',
			items: [
				{
					key: 'mirostat',
					type: 'range',
					def: 0,
					range: { min: 0, max: 2, step: 1 },
					num: { min: 0, max: 2, step: 1 }
				},
				{
					key: 'mirostat_eta',
					type: 'range',
					def: 0.1,
					range: { min: 0, max: 1, step: 0.05 },
					num: { min: 0, max: 1, step: 'any' }
				},
				{
					key: 'mirostat_tau',
					type: 'range',
					def: 5.0,
					range: { min: 0, max: 10, step: 0.5 },
					num: { min: 0, max: 10, step: 'any' }
				}
			]
		},
		{
			title: 'Ollama',
			items: [
				{
					key: 'num_keep',
					type: 'range',
					def: 24,
					range: { min: -1, max: 10240000, step: 1 },
					num: { min: -1, step: 1 }
				},
				{
					key: 'num_ctx',
					type: 'range',
					def: 2048,
					range: { min: -1, max: 10240000, step: 1 },
					num: { min: -1, step: 1 }
				},
				{
					key: 'num_batch',
					type: 'range',
					def: 512,
					range: { min: 256, max: 8192, step: 256 },
					num: { min: 256, step: 256 }
				},
				{
					key: 'use_mmap',
					type: 'switch',
					def: true,
					admin: true
				},
				{
					key: 'use_mlock',
					type: 'switch',
					def: true,
					admin: true
				},
				{
					key: 'num_thread',
					type: 'range',
					def: 2,
					range: { min: 1, max: 256, step: 1 },
					num: { min: 1, max: 256, step: 1 },
					admin: true
				},
				{
					key: 'num_gpu',
					type: 'range',
					def: 0,
					range: { min: 0, max: 256, step: 1 },
					num: { min: 0, max: 256, step: 1 },
					admin: true
				}
			]
		}
	];

	const isSet = (item: any) => (params?.[item.key] ?? null) !== null;

	// 書き込みは「新 object への再代入」にして bind:params 伝播とリアクティブを確実にする。
	const setVal = (item: any, value: any) => {
		params = { ...params, [item.key]: value };
	};

	const toggleDefault = (item: any) => {
		setVal(item, isSet(item) ? null : item.def);
	};

	const cycle = (item: any) => {
		const cur = params?.[item.key] ?? null;
		const idx = item.states.findIndex((s: any) => s.value === cur);
		setVal(item, item.states[(idx + 1) % item.states.length].value);
	};

	// 重要: Svelte(legacy)のテンプレート式は「マークアップに直接書かれた変数」しか追跡しない。
	// params の読み取りをヘルパ関数に隠すと untrack され再描画されない(値変更が反映されない)。
	// そのため下のテンプレートは params[item.key] を直接参照し、現在値は引数で渡す。
	const cycleLabel = (item: any, cur: any) =>
		item.states.find((s: any) => s.value === (cur ?? null))?.label ?? 'Default';

	const setNumber = (item: any, e: any) => {
		const n = e.currentTarget.valueAsNumber;
		setVal(item, Number.isNaN(n) ? null : n);
	};
	const setText = (item: any, e: any) => {
		setVal(item, e.currentTarget.value);
	};
	const setSwitch = (item: any, value: any) => {
		setVal(item, value);
	};

	$: if (params) {
		dispatch('change', params);
	}
</script>

<div class=" space-y-1 text-sm pb-safe-bottom">
	{#each sections as section, sectionIndex}
		<div
			class="text-md font-semibold text-content-muted border-b border-gray-100 dark:border-gray-800 pb-1 mb-2 {sectionIndex >
			0
				? 'mt-4'
				: ''}"
		>
			{section.title}
		</div>

		<!-- 1col だと横に伸びて視認性が落ちるので自動で列分けする。max は 1fr にすること:
		     固定 px を max にすると auto-fill がその px 単位で本数を数え、幅が足りず 1col に潰れる。 -->
		<div class="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-x-6">
			{#each section.items as item (item.key)}
				{#if !item.admin || admin}
					<!-- キー(左)と値(右)を justify-between で両端寄せ。行レイアウトはこの 1 ブロックだけ編集すれば全項目に反映される -->
					<div class=" py-0.5 flex items-center justify-between gap-2">
						<!-- ラベルは折り返さない(shrink-0 + nowrap)。不足分は右側の slider が縮んで吸収する -->
						<div class=" self-center text-sm font-medium whitespace-nowrap shrink-0">
							{item.key}
						</div>

						<div class="flex items-center gap-1 min-w-0">
							{#if item.type === 'cycle'}
								<button
									class="p-1 px-1 text-sm flex rounded-sm transition shrink-0 outline-hidden"
									type="button"
									on:click={() => cycle(item)}
								>
									<span class="self-center">{cycleLabel(item, params[item.key])}</span>
								</button>
							{:else}
								<button
									class="p-1 px-1 text-sm flex rounded-sm transition shrink-0 outline-hidden"
									type="button"
									on:click={() => toggleDefault(item)}
								>
									{#if (params[item.key] ?? null) !== null}
										<span class="i-lucide-x size-4 self-center block"></span>
									{:else}
										<span class="self-center">Default</span>
									{/if}
								</button>

								{#if (params[item.key] ?? null) !== null}
									{#if item.type === 'range'}
										<div class="flex items-center gap-1 min-w-0">
											<div class="w-32 min-w-0 shrink">
												<input
													type="range"
													min={item.range.min}
													max={item.range.max}
													step={item.range.step}
													value={params[item.key]}
													on:input={(e) => setNumber(item, e)}
													class="w-full h-2 rounded-lg appearance-none cursor-pointer bg-gray-100 dark:bg-gray-700"
												/>
											</div>
											<input
												type="number"
												value={params[item.key]}
												on:input={(e) => setNumber(item, e)}
												class=" bg-transparent text-right px-0.5"
												style="field-sizing: content;"
												min={item.num.min}
												max={item.num.max}
												step={item.num.step}
											/>
										</div>
									{:else if item.type === 'input'}
										<div class="w-32 min-w-0 shrink">
											<input
												type={item.inputType}
												placeholder={item.placeholder}
												value={params[item.key]}
												on:input={(e) =>
													item.inputType === 'number' ? setNumber(item, e) : setText(item, e)}
												class="w-full rounded-lg py-1 px-1 text-sm text-content dark:bg-gray-850 outline-hidden"
												autocomplete="off"
												min={item.min}
											/>
										</div>
									{:else if item.type === 'switch'}
										<div class="text-sm text-content-muted">
											{params[item.key] ? 'Enabled' : 'Disabled'}
										</div>
										<div class=" pr-2">
											<Switch
												state={params[item.key]}
												on:change={(e) => setSwitch(item, e.detail)}
											/>
										</div>
									{/if}
								{/if}
							{/if}
						</div>
					</div>
				{/if}
			{/each}
		</div>
	{/each}
</div>
