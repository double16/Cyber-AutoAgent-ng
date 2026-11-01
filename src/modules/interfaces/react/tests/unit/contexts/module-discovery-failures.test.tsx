import React from 'react';
import {afterEach, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from '../test-renderer.js';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const readdir = jest.fn<any>(); const readFile = jest.fn<any>(); const loadYaml = jest.fn<any>();
jest.unstable_mockModule('fs/promises', () => ({readdir, readFile}));
jest.unstable_mockModule('js-yaml', () => ({load: loadYaml}));
const {ModuleProvider, useModule} = await import('../../../src/contexts/ModuleContext.js');
const env = {...process.env};
afterEach(() => {process.env = {...env}; jest.clearAllMocks();});
it.each(['empty', 'valid', 'invalid', 'missing', 'no-tools'])('handles %s module manifests with documented fallbacks', async scenario => {
  process.env.CYBER_MODULE_PATH = '~:~/mods:relative:relative'; process.env.CYBER_CONFIG_DIR = '/config'; process.env.DEBUG = '1';
  const dir = (name: string, directory: boolean) => ({name, isDirectory: () => directory});
  readdir.mockImplementation(async (p: string, options: any) => {
    if (scenario === 'empty') throw new Error('unreadable');
    if (options?.withFileTypes) {
      if (p.endsWith('/mods') || p.endsWith('/relative')) return [dir('cloud', true)];
      if (p.endsWith('/cloud')) return [dir('module.yaml', false)];
      return [];
    }
    if (scenario === 'no-tools') throw new Error('missing tools');
    return ['scan.py', 'notes.txt'];
  });
  readFile.mockImplementation(async () => {if (scenario === 'missing') throw new Error('missing manifest'); return 'manifest';});
  loadYaml.mockImplementation(() => {if (scenario === 'invalid') throw new Error('bad yaml'); return {};});
  let context!: ReturnType<typeof useModule>; const Consumer = () => {context = useModule(); return null;};
  let view!: ReturnType<typeof TestRenderer.create>;
  await act(async () => {view = TestRenderer.create(<ModuleProvider><Consumer/></ModuleProvider>);});
  if (['valid', 'no-tools'].includes(scenario)) {
    expect(context.currentModule).toBe('cloud');
    expect(context.moduleInfo).toMatchObject({description: 'cloud module', category: 'security', capabilities: []});
    expect(context.suggestModuleForObjective('unknown objective')).toBe('cloud');
    expect(context.suggestModuleForObjective('cloud')).toBe('cloud');
    if (scenario === 'valid') expect(context.moduleInfo?.tools[0]).toMatchObject({description: 'scan', category: 'general'});
  } else {
    expect(context.availableModules).toEqual({}); expect(context.suggestModuleForObjective('anything')).toBe('web');
  }
  act(() => view.unmount());
});
