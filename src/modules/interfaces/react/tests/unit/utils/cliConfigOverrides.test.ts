import {describe, expect, it} from '@jest/globals';
import {applyMemoryModeOverride} from '../../../src/utils/cliConfigOverrides.js';

describe('applyMemoryModeOverride', () => {
  it('makes shared CLI scope override saved configuration', () => {
    const overrides: Record<string, unknown> = {};

    applyMemoryModeOverride(overrides, 'shared');

    expect(overrides).toEqual({memoryMode: 'shared'});
  });

  it('does not add an override when the CLI flag is absent', () => {
    const overrides: Record<string, unknown> = {};

    applyMemoryModeOverride(overrides, undefined);

    expect(overrides).toEqual({});
  });

  it('rejects unsupported memory scopes', () => {
    expect(() => applyMemoryModeOverride({}, 'auto')).toThrow(
      'Invalid memory mode: auto. Expected shared or operation.',
    );
  });
});
