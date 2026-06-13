/**
 * Unit tests for ModelsCatalog.ts loader and helpers
 */
import { describe, it, expect, beforeEach, jest } from '@jest/globals';

// Helper to create a minimal catalog JSON blob
function makeCatalog(data?: Partial<Record<string, any>>) {
  return JSON.stringify({
    openai: {
      id: 'openai',
      name: 'OpenAI',
      api: 'openai',
      models: {
        'openai/gpt-4.1-mini': {
          id: 'openai/gpt-4.1-mini',
          name: 'GPT-4.1 Mini',
          limit: { context: 128000, output: 8192 },
          cost: { input: 3, output: 15 },
        },
        'gpt-mini': {
          id: 'gpt-mini',
          name: 'GPT Mini',
          limit: { context: 32000, output: 4096 },
          cost: { input: 2 }, // output implied = input, cache_* derived
        },
      },
    },
    ...(data || {}),
  });
}

describe('ModelsCatalog loader and helpers', () => {
  beforeEach(() => {
    jest.resetModules();
    jest.clearAllMocks();
    delete (process as any).env.MODELS_DEV_CACHE_PATH;
    process.env.DEV_CLIENT_OFFLINE = 'true'; // ensure live API tier is skipped
  });

  it('exposes models via getAllModels/peekAllModels when catalog is preloaded', async () => {
    const { modelsCatalog } = await import('../../../src/services/ModelsCatalog.js');
    // Preload the in-memory catalog directly to avoid I/O
    (modelsCatalog as any).catalog = JSON.parse(makeCatalog());

    const all = await modelsCatalog.getAllModels();
    expect(all.length).toBeGreaterThan(0);
    const entry = all.find(x => x.model.id === 'openai/gpt-4.1-mini');
    expect(entry).toBeTruthy();
    expect(entry!.provider).toBe('openai');
    expect(entry!.model.limit!.context).toBe(128000);

    const peek = modelsCatalog.peekAllModels();
    expect(peek).not.toBeNull();
    expect(peek!.some(x => x.model.name === 'GPT-4.1 Mini')).toBe(true);
  });


  it('getContextLimit and getContextLimitSync return context window when available', async () => {
    const { getContextLimit, getContextLimitSync, modelsCatalog } = await import('../../../src/services/ModelsCatalog.js');

    // Before load, sync returns null
    expect(getContextLimitSync('openai/gpt-4.1-mini')).toBeNull();

    (modelsCatalog as any).catalog = JSON.parse(makeCatalog());

    // After load, both async and sync should return values
    const limitAsync = await getContextLimit('openai/gpt-4.1-mini');
    expect(limitAsync).toBe(128000);

    const limitSync = getContextLimitSync('openai/gpt-4.1-mini');
    expect(limitSync).toBe(128000);
  });

  it('findModel supports matching by last path segment heuristic', async () => {
    const { modelsCatalog } = await import('../../../src/services/ModelsCatalog.js');
    (modelsCatalog as any).catalog = JSON.parse(makeCatalog());

    // Catalog has model id 'gpt-mini'; we query with a doubled provider prefix to trigger shortId match
    const found = await modelsCatalog.findModel('openai/openai/gpt-mini');
    expect(found).not.toBeNull();
    expect(found!.model.id).toBe('gpt-mini');
  });
});
