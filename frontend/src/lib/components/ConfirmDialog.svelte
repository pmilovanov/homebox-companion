<script lang="ts">
	import { TriangleAlert } from 'lucide-svelte';
	import Button from './Button.svelte';
	import Card from './Card.svelte';

	interface Props {
		open: boolean;
		title: string;
		message: string;
		/** Something to know before confirming, shown in warning colour under the message. */
		warning?: string | null;
		confirmLabel?: string;
		cancelLabel?: string;
		onConfirm: () => void;
		onCancel: () => void;
	}

	let {
		open = false,
		title,
		message,
		warning = null,
		confirmLabel = 'Confirm',
		cancelLabel = 'Cancel',
		onConfirm,
		onCancel,
	}: Props = $props();

	function handleBackdropClick(event: MouseEvent) {
		if (event.target === event.currentTarget) {
			onCancel();
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			onCancel();
		}
	}
</script>

<svelte:window on:keydown={handleKeydown} />

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="animate-in fixed inset-0 z-50 flex items-center justify-center bg-neutral-950/60 backdrop-blur-sm"
		onclick={handleBackdropClick}
	>
		<div class="mx-4 w-full max-w-sm">
			<Card padding="lg">
				<h2 id="dialog-title" class="mb-2 text-h3 text-neutral-100">
					{title}
				</h2>
				<p class="text-body text-neutral-400 {warning ? 'mb-3' : 'mb-6'}">
					{message}
				</p>
				{#if warning}
					<p class="mb-6 flex items-start gap-2 text-body-sm text-warning-400" role="alert">
						<TriangleAlert size={16} strokeWidth={2} class="mt-0.5 shrink-0" />
						<span>{warning}</span>
					</p>
				{/if}
				<div class="flex gap-3">
					<Button variant="secondary" full onclick={onCancel}>
						{cancelLabel}
					</Button>
					<Button variant="primary" full onclick={onConfirm}>
						{confirmLabel}
					</Button>
				</div>
			</Card>
		</div>
	</div>
{/if}
