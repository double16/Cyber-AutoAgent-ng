import {afterEach, beforeEach, expect, it, jest} from '@jest/globals';
const readFile = jest.fn<any>();
jest.unstable_mockModule('fs/promises', () => ({readFile}));
const catalog = {empty: {}, vendor: {models: {one: {id: 'one', name: 'Model One', limit: {context: 4096}}, two: {id: 'two'}}}};
const env = {...process.env}; const originalFetch = globalThis.fetch;
beforeEach(() => {jest.resetModules(); readFile.mockReset(); delete process.env.MODELS_DEV_CACHE_PATH; delete process.env.DEV_CLIENT_OFFLINE;});
afterEach(() => {process.env = {...env}; globalThis.fetch = originalFetch;});
it('loads a cache file once for concurrent callers and handles missing model metadata', async () => {
  process.env.MODELS_DEV_CACHE_PATH = '/cache/catalog.json';
  readFile.mockResolvedValue(JSON.stringify(catalog));
  const {modelsCatalog, loadAllModels, peekAllModels, getContextLimit, getContextLimitSync} = await import('../../../src/services/ModelsCatalog.js');
  expect(peekAllModels()).toBeNull();
  const [a,b] = await Promise.all([modelsCatalog.getCatalog(), modelsCatalog.getCatalog()]);
  expect(a).toBe(b); expect(readFile).toHaveBeenCalledTimes(1);
  expect(await loadAllModels()).toHaveLength(2); expect(peekAllModels()).toHaveLength(2);
  expect(await modelsCatalog.findModel('prefix/Model One')).toEqual({provider: 'vendor', model: catalog.vendor.models.one});
  expect(await modelsCatalog.findModel('region.provider.one')).toBeTruthy();
  expect(await modelsCatalog.findModel('no-such-model')).toBeNull();
  expect(await getContextLimit('two')).toBeNull(); expect(getContextLimitSync('two')).toBeNull();
  expect(getContextLimitSync('missing')).toBeNull();
});
it('uses the live catalog after an invalid cache', async () => {
  process.env.MODELS_DEV_CACHE_PATH = '/bad'; readFile.mockRejectedValue(new Error('missing'));
  const fetch = jest.fn<any>().mockResolvedValue({ok: true, json: async () => catalog}); globalThis.fetch = fetch;
  const {modelsCatalog} = await import('../../../src/services/ModelsCatalog.js');
  await expect(modelsCatalog.getCatalog()).resolves.toEqual(catalog);
  expect(fetch).toHaveBeenCalledWith('https://models.dev/api.json', {method: 'GET'});
});
it.each(['error', 'status', 'offline'])('falls back to embedded snapshot after %s', async reason => {
  globalThis.fetch = jest.fn<any>().mockImplementation(async () => {if (reason === 'error') throw new Error('offline'); return {ok: false};});
  if (reason === 'offline') process.env.DEV_CLIENT_OFFLINE = 'true';
  readFile.mockRejectedValueOnce(new Error('first path missing')).mockResolvedValueOnce(JSON.stringify(catalog));
  const {modelsCatalog} = await import('../../../src/services/ModelsCatalog.js');
  await expect(modelsCatalog.getCatalog()).resolves.toEqual(catalog);
  expect(readFile).toHaveBeenCalledTimes(2);
});
it('returns an empty catalog when offline snapshots are missing', async () => {
  process.env.DEV_CLIENT_OFFLINE = 'true'; readFile.mockRejectedValue(new Error('missing'));
  const {modelsCatalog} = await import('../../../src/services/ModelsCatalog.js');
  await expect(modelsCatalog.getAllModels()).resolves.toEqual([]);
});
