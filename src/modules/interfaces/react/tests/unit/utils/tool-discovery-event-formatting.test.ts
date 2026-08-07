import { describe, expect, it } from '@jest/globals';
import { formatToolDiscoveryEvent } from '../../../src/utils/toolDiscoveryEventFormatting.js';

describe('formatToolDiscoveryEvent', () => {
  it.each([
    [{ type: 'tool_discovery_start' }, '🔎 Loading cybersecurity assessment tools'],
    [{ type: 'tool_available', tool_name: 'scanner', description: 'Scan hosts' }, '  🔧 scanner (Scan hosts)'],
    [{ type: 'tool_unavailable', tool_name: 'browser' }, '  ⛔ browser - unavailable'],
    [{ type: 'environment_ready', tool_count: 2 }, '🟢 Environment ready - 2 cybersecurity tools loaded'],
  ])('formats %o for the auto-run console', (event, expected) => {
    expect(formatToolDiscoveryEvent(event)).toBe(expected);
  });

  it('handles missing event fields and ignores unrelated events', () => {
    expect(formatToolDiscoveryEvent({ type: 'tool_available' })).toBe('  🔧 unnamed tool');
    expect(formatToolDiscoveryEvent({ type: 'unknown' })).toBeNull();
    expect(formatToolDiscoveryEvent(null)).toBeNull();
  });
});
