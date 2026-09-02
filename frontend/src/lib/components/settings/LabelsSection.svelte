<script lang="ts">
	/**
	 * LabelsSection - Pre-printed asset ID labels.
	 *
	 * Shows whether Homebox's own asset-ID numbering is parked above the printed
	 * labels, and parks it when it is not, by creating the sentinel item.
	 * Rendered only when the server has label detection and the sentinel on.
	 */
	import { onMount } from 'svelte';
	import { Tag, Check, TriangleAlert } from 'lucide-svelte';
	import Button from '$lib/components/Button.svelte';
	import { assetIdSentinel } from '$lib/stores/assetIdSentinel.svelte';
	import { showToast } from '$lib/stores/ui.svelte';

	const store = assetIdSentinel;

	onMount(() => {
		// Always fresh here: this is where it gets acted on.
		void store.load(true);
	});

	async function createSentinel() {
		const created = await store.create();
		if (created) {
			showToast('Sentinel created. Homebox now numbers new items above your labels.', 'success');
		} else if (store.error) {
			showToast(store.error, 'error');
		}
	}
</script>

{#if store.status?.enabled}
	<section class="card space-y-4">
		<h2 class="flex items-center gap-2 text-body-lg font-semibold text-neutral-100">
			<Tag class="h-5 w-5 text-primary-400" strokeWidth={1.5} />
			Pre-printed Labels
		</h2>

		<p class="text-body-sm text-neutral-400">
			Homebox numbers items itself, always the next number above the highest asset ID it holds. A
			sentinel item parked above every printed label keeps those numbers off your stickers.
		</p>

		{#if store.status.ok}
			<p class="flex items-start gap-2 text-body-sm text-success-500">
				<Check size={16} strokeWidth={2} class="mt-0.5 shrink-0" />
				<span>Sentinel in place. Highest asset ID in Homebox: {store.status.highest}.</span>
			</p>
		{:else}
			<p class="flex items-start gap-2 text-body-sm text-warning-400" role="alert">
				<TriangleAlert size={16} strokeWidth={2} class="mt-0.5 shrink-0" />
				<span>
					No sentinel. The highest asset ID in Homebox is {store.status.highest ?? 'none'}, below
					{store.status.sentinel}, so Homebox can hand a label's number to an item that has no
					label.
				</span>
			</p>
			<Button
				variant="primary"
				onclick={createSentinel}
				loading={store.loading}
				disabled={store.loading}
			>
				Create sentinel item
			</Button>
			<p class="text-caption text-neutral-500">
				Creates an archived item named "Asset ID sentinel" with asset ID {store.status.sentinel}. Do
				not delete it.
			</p>
		{/if}

		{#if store.error}
			<p class="text-body-sm text-error-500">{store.error}</p>
		{/if}
	</section>
{/if}
