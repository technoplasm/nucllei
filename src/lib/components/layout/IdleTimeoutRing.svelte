<script lang="ts">
	// アイドルタイムアウト(SESSION_IDLE_TIMEOUT)までの残り時間を、リングと文字(`m:ss`)で出す。
	// 置き場所は各画面の右上のボタン群の**左端**(chat の Navbar では「…」の左、workspace では
	// 上部メニューの右端)。ボタン群の兄弟と同じ高さ・角丸・ホバー背景の 1 個のボタンとして並ぶ。
	//
	// - 残りは $lib/apis/fetch の getIdleRemaining() — **失効の判定と同じ値**(全タブ共有の
	//   最終操作時刻 + TTL と、知っているサーバの期限の早い方)。サーバ側が先に切れるなら
	//   その時間は出さない。どのタブでも同じ残りになり、どこかで操作すれば全タブで(ほぼ)満タンに
	//   戻る(サーバの期限は操作 + TTL から最大で延命の刻み = 1 分手前)。
	//   0 になる瞬間に fetch.ts の判定もちょうど走る(scheduleCheck)。
	// - 普段は隠す。カーソルを重ねると出る(重ねるだけでは「操作」に数えない — 延命しない)。
	//   残りが 1/2 を切ったら常に出す(ホバーの無いモバイルはここからだけ見える)。
	//   色は割合で段階を付ける: 1/2 未満はまだ普段の色、1/4 未満で黄、1/8 未満で赤(idleRingLevel)。
	// - クリックは「操作」として数える(noteInteraction = キー・クリックと同じ経路。サーバ側の
	//   延命も既存の keepalive に乗るだけで、新しい延命の仕組みは持たない)。
	//
	// **隠すのは opacity だけ。枠(幅・高さ)は常に同じ場所を占める** — 出る・消える・カウント
	// ダウンで、右の「…」以降のボタンも左の見出しも 1px も動かない(タッチ端末も同じ)。
	// 文字の幅は全長の表示の最も長い形(idleRingWidestText、`10m` なら `00:00`)で常に確保し、
	// 実際の文字はその中でリング側へ寄せる。
	import { getContext, untrack } from 'svelte';

	import { config, user } from '$lib/stores';
	import { getIdleRemaining, noteInteraction, type IdleRemaining } from '$lib/apis/fetch';
	import {
		formatIdleRemaining,
		idleRingFraction,
		idleRingWidestText,
		idleRingLevel,
		isIdleRingVisible,
		msUntilNextIdleTick
	} from '$lib/utils/idle-ring';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n: any = getContext('i18n');

	// リング(進捗の図形。アイコンではない)の寸法。右上のアイコン(size-5 = 20px)とそろえる
	const RING_SIZE = 20;
	const RING_STROKE = 2.5;
	const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
	const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

	// root layout の startIdleWatch と同じ条件(ログイン中で、タイムアウトが有限)
	const timeoutSeconds = $derived($user ? ($config?.session_idle_timeout ?? 0) : 0);
	const enabled = $derived(timeoutSeconds > 0);
	// 枠が常に確保する文字の幅(監視の起動前から決まる = 最初の描画から枠の幅が動かない)
	const widestText = $derived(idleRingWidestText(timeoutSeconds * 1000));

	let idle = $state<IdleRemaining | null>(null);
	let hovered = $state(false);
	let focusVisible = $state(false);

	const visible = $derived(
		idle
			? isIdleRingVisible({
					remainingMs: idle.remainingMs,
					timeoutMs: idle.timeoutMs,
					hovered: hovered || focusVisible
				})
			: false
	);
	const level = $derived(idle ? idleRingLevel(idle.remainingMs, idle.timeoutMs) : 'normal');
	// 色の段階(文字とリングの両方に currentColor で効く)。黄・赤は既存の注意 / 警告の色と同じ濃さ
	const LEVEL_CLASS = {
		normal: 'text-content-muted',
		caution: 'text-amber-600 dark:text-amber-400',
		danger: 'text-red-600 dark:text-red-400'
	} as const;
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

	const extend = () => {
		// window の capture リスナ(fetch.ts)も同じクリックを数えるが、明示しておく
		// (二重に呼んでも keepalive は連打防止で 1 本)
		noteInteraction();
		tick();
	};
</script>

{#if enabled}
	<Tooltip content={tooltip} placement="bottom">
		<button
			type="button"
			class="flex h-9 shrink-0 cursor-pointer items-center gap-1 rounded-xl px-2 transition-opacity duration-200 hover:bg-surface-hover {visible
				? 'opacity-100'
				: 'opacity-0'} {LEVEL_CLASS[level]}"
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
			<!-- 残り時間の文字。最も長い形の幅を常に取り(見えない sizer と同じセルに重ねる)、
			     実際の文字はリング側へ寄せる -->
			<span class="grid text-sm font-medium tabular-nums whitespace-nowrap">
				<span class="invisible col-start-1 row-start-1" aria-hidden="true">{widestText}</span>
				<span class="col-start-1 row-start-1 justify-self-end">{remainingText}</span>
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
{/if}
