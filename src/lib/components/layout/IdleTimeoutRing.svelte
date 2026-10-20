<script lang="ts">
	// アイドルタイムアウト(SESSION_IDLE_TIMEOUT)までの残り時間を、画面右上のリングと文字で出す。
	//
	// - 残りは $lib/apis/fetch の getIdleRemaining() — **失効の判定と同じ値**(全タブ共有の
	//   最終操作時刻 + TTL)。どのタブでも同じ残りになり、どこかで操作すれば全タブで満タンに戻る。
	//   0 になる瞬間に fetch.ts の判定もちょうど走る(scheduleCheck)。
	// - 普段は隠す。カーソルを重ねると出る(重ねるだけでは「操作」に数えない — 延命しない)。
	//   残りが 1/3 を切ったら常に出す(ホバーの無いモバイルはここからだけ見える)。
	// - クリックは「操作」として数える(noteInteraction = キー・クリックと同じ経路。サーバ側の
	//   延命も既存の keepalive に乗るだけで、新しい延命の仕組みは持たない)。
	//
	// 右上の他の操作部品と重ならないよう、占める幅を CSS 変数 `--idle-ring-reserve`
	// (画面右端からの幅、px。出さないときは未設定 = 0)で知らせる。各画面の右上の行
	// (chat の Navbar・workspace の上部メニュー・右ペインの見出し)はその幅を空ける。
	// 隠れている間に空けるのはリング 1 個分(ホバーの的)だけ。文字の分は常に出る段階でだけ空け、
	// ホバー中の文字はツールチップと同じく一時的に手前へ重ねる。
	import { getContext, onDestroy, untrack } from 'svelte';

	import { config, user } from '$lib/stores';
	import { getIdleRemaining, noteInteraction, type IdleRemaining } from '$lib/apis/fetch';
	import {
		formatIdleRemaining,
		idleRingFraction,
		isIdleRingPinned,
		isIdleRingVisible,
		isIdleRingWarning,
		msUntilNextIdleTick
	} from '$lib/utils/idle-ring';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n: any = getContext('i18n');

	// 画面右端からの余白(class の right-2.5 と同じ値)。Navbar 等の右端の余白とそろえてある
	const RIGHT_INSET_PX = 10;
	// リング(進捗の図形。アイコンではない)の寸法。右上のアイコン(size-5 = 20px)とそろえる
	const RING_SIZE = 20;
	const RING_STROKE = 2.5;
	const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
	const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

	// root layout の startIdleWatch と同じ条件(ログイン中で、タイムアウトが有限)
	const enabled = $derived(!!$user && ($config?.session_idle_timeout ?? 0) > 0);

	let idle = $state<IdleRemaining | null>(null);
	let hovered = $state(false);
	let focusVisible = $state(false);
	// ホバーできる端末か。できない(タッチ)端末では、隠れている間の的を置かない・幅も空けない
	let canHover = $state(true);
	let widgetWidth = $state(0);

	const pinned = $derived(idle ? isIdleRingPinned(idle.remainingMs, idle.timeoutMs) : false);
	const visible = $derived(
		idle
			? isIdleRingVisible({
					remainingMs: idle.remainingMs,
					timeoutMs: idle.timeoutMs,
					hovered: hovered || focusVisible
				})
			: false
	);
	const warning = $derived(idle ? isIdleRingWarning(idle.remainingMs, idle.timeoutMs) : false);
	const fraction = $derived(idle ? idleRingFraction(idle.remainingMs, idle.timeoutMs) : 0);
	const remainingText = $derived(idle ? formatIdleRemaining(idle.remainingMs) : '');
	const tooltip = $derived(
		idle
			? $i18n.t(
					'You will be signed out automatically in {{time}} without any activity. Click to extend.',
					{ time: remainingText }
				)
			: ''
	);
	// 隠れている間も的として触れるのはホバーできる端末だけ
	const interactive = $derived(pinned || canHover);

	let tickTimer: ReturnType<typeof setTimeout> | null = null;

	const clearTick = () => {
		if (tickTimer) {
			clearTimeout(tickTimer);
			tickTimer = null;
		}
	};

	// 1 秒に 1 回、表示の秒が切り替わる瞬間に読み直す(状態は idle 1 つだけ)。背面タブでは止める
	const tick = () => {
		clearTick();
		const next = getIdleRemaining();
		// 失効の判定が先に走ると監視が止まって null になる — 遷移までの間は 0 のまま見せる
		idle = next ?? (idle ? { ...idle, remainingMs: 0 } : null);
		if (document.visibilityState !== 'visible') {
			return;
		}
		// まだ監視が始まっていない(root layout の起動待ち)ときは 1 秒後に読み直す
		tickTimer = setTimeout(tick, next ? msUntilNextIdleTick(next.remainingMs) + 5 : 1000);
	};

	const onVisibilityChange = () => {
		if (document.visibilityState === 'visible') {
			tick();
		} else {
			clearTick();
		}
	};

	$effect(() => {
		if (!enabled) {
			idle = null;
			return;
		}
		// tick は idle を読み書きする — 追跡すると毎秒この effect が張り直される
		untrack(tick);
		document.addEventListener('visibilitychange', onVisibilityChange);
		return () => {
			clearTick();
			document.removeEventListener('visibilitychange', onVisibilityChange);
		};
	});

	$effect(() => {
		const media = window.matchMedia('(hover: hover)');
		canHover = media.matches;
		const onChange = () => {
			canHover = media.matches;
		};
		media.addEventListener('change', onChange);
		return () => media.removeEventListener('change', onChange);
	});

	// 右上の行に空けてもらう幅(変わったときだけ書く — $derived の等価判定)
	const reserveWidth = $derived(idle && interactive ? widgetWidth : 0);
	$effect(() => {
		const root = document.documentElement.style;
		if (reserveWidth > 0) {
			root.setProperty('--idle-ring-reserve', `${reserveWidth + RIGHT_INSET_PX}px`);
		} else {
			root.removeProperty('--idle-ring-reserve');
		}
	});

	onDestroy(() => {
		clearTick();
		document.documentElement.style.removeProperty('--idle-ring-reserve');
	});

	const extend = () => {
		// window の capture リスナ(fetch.ts)も同じクリックを数えるが、明示しておく
		// (二重に呼んでも keepalive は連打防止で 1 本)
		noteInteraction();
		tick();
	};
</script>

{#if enabled && idle}
	<div
		class="fixed top-2 right-2.5 z-[35] flex h-9 items-center pointer-events-none"
		bind:offsetWidth={widgetWidth}
	>
		<Tooltip content={tooltip} placement="bottom-end" className="flex">
			<button
				type="button"
				class="relative flex h-9 items-center gap-1 rounded-xl {pinned
					? 'pl-1.5'
					: 'pl-2'} pr-2 transition-opacity duration-200 hover:bg-surface-hover {interactive
					? 'pointer-events-auto cursor-pointer'
					: 'pointer-events-none'} {visible ? 'opacity-100' : 'opacity-0'} {warning
					? 'text-red-600 dark:text-red-400'
					: 'text-content-muted'}"
				aria-label={tooltip}
				onclick={extend}
				onpointerenter={(e) => {
					if (e.pointerType !== 'touch') {
						hovered = true;
					}
				}}
				onpointerleave={() => {
					hovered = false;
				}}
				onfocus={(e) => {
					focusVisible = e.currentTarget.matches(':focus-visible');
				}}
				onblur={() => {
					focusVisible = false;
				}}
			>
				<!-- 残り時間の文字。常に出る段階では幅を取って並べ、ホバーで出すときはリングの左へ
				     重ねる(的はリングだけ — 文字の上を通っても隠れたまま) -->
				<span
					class="text-sm font-medium tabular-nums whitespace-nowrap {pinned
						? ''
						: 'pointer-events-none absolute right-full top-1/2 -translate-y-1/2 rounded-lg bg-surface px-1.5 py-0.5 shadow-sm'}"
				>
					{remainingText}
				</span>
				<!-- 進捗の図形(アイコンではない): 満タン = TTL、空 = ログアウト -->
				<svg
					class="block shrink-0 -rotate-90"
					width={RING_SIZE}
					height={RING_SIZE}
					viewBox="0 0 {RING_SIZE} {RING_SIZE}"
					aria-hidden="true"
				>
					<circle
						cx={RING_SIZE / 2}
						cy={RING_SIZE / 2}
						r={RING_RADIUS}
						fill="none"
						stroke="currentColor"
						stroke-opacity="0.2"
						stroke-width={RING_STROKE}
					/>
					<circle
						class="transition-[stroke-dashoffset] duration-1000 ease-linear"
						cx={RING_SIZE / 2}
						cy={RING_SIZE / 2}
						r={RING_RADIUS}
						fill="none"
						stroke="currentColor"
						stroke-width={RING_STROKE}
						stroke-linecap="round"
						stroke-opacity={fraction > 0 ? 1 : 0}
						stroke-dasharray={RING_CIRCUMFERENCE}
						stroke-dashoffset={RING_CIRCUMFERENCE * (1 - fraction)}
					/>
				</svg>
			</button>
		</Tooltip>
	</div>
{/if}
