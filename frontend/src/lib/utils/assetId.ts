/**
 * Homebox stores an asset ID as an integer and ignores hyphens on the way in,
 * so "900-26843450000", "90026843450000" and "000-042" / "42" each name one ID.
 * Compare IDs on this key, never on the typed text.
 */
export function assetIdKey(assetId: string): string {
	const compact = assetId.replace(/[-\s]/g, '');
	return /^[0-9]+$/.test(compact) ? compact.replace(/^0+(?=[0-9])/, '') : compact;
}
