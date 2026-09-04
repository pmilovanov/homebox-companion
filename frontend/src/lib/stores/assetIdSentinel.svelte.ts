/**
 * Asset ID sentinel store
 *
 * Whether the sentinel item that keeps Homebox's own asset-ID numbering above
 * the pre-printed labels is in place. Fetched once per session: the capture
 * screen warns from it, the settings page acts on it.
 */
import { items as itemsApi, type AssetIdSentinelStatus } from '$lib/api/items';
import { createLogger } from '$lib/utils/logger';

const log = createLogger({ prefix: 'AssetIdSentinel' });

class AssetIdSentinelStore {
	private _status = $state<AssetIdSentinelStatus | null>(null);
	private _loading = $state(false);
	private _error = $state<string | null>(null);

	/** Deduplicates concurrent loads. Internal bookkeeping, not reactive state. */
	private _pending: Promise<void> | null = null;

	get status(): AssetIdSentinelStatus | null {
		return this._status;
	}

	get loading(): boolean {
		return this._loading;
	}

	get error(): string | null {
		return this._error;
	}

	/** Labels are on, and Homebox's numbering is not yet parked above them. */
	get needsAttention(): boolean {
		return this._status?.enabled === true && !this._status.ok;
	}

	/** Fetch the status; cached after the first success unless forced. */
	async load(force = false): Promise<void> {
		if (this._status && !force) return;
		if (this._pending) return this._pending;
		this._pending = this.fetch();
		try {
			await this._pending;
		} finally {
			this._pending = null;
		}
	}

	private async fetch(): Promise<void> {
		this._loading = true;
		this._error = null;
		try {
			this._status = await itemsApi.sentinelStatus();
		} catch (error) {
			this._error = error instanceof Error ? error.message : 'Could not check the sentinel';
			log.warn('Sentinel check failed', error);
		} finally {
			this._loading = false;
		}
	}

	/** Create the sentinel item. Returns whether one was created. */
	async create(): Promise<boolean> {
		this._loading = true;
		this._error = null;
		try {
			const result = await itemsApi.createSentinel();
			this._status = result;
			return result.created;
		} catch (error) {
			this._error = error instanceof Error ? error.message : 'Could not create the sentinel';
			log.error('Sentinel creation failed', error);
			return false;
		} finally {
			this._loading = false;
		}
	}
}

export const assetIdSentinel = new AssetIdSentinelStore();
