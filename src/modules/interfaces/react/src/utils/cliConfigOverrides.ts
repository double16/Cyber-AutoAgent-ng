import type {Config} from '../contexts/ConfigContext.js';

/** Apply the memory scope supplied on the React CLI command line. */
export function applyMemoryModeOverride(
  configOverrides: Partial<Config>,
  memoryMode: string | undefined,
): void {
  if (memoryMode === undefined) return;

  if (memoryMode !== 'shared' && memoryMode !== 'operation') {
    throw new Error(`Invalid memory mode: ${memoryMode}. Expected shared or operation.`);
  }

  configOverrides.memoryMode = memoryMode;
}
