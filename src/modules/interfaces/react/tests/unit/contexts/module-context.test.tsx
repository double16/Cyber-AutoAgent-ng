import React, {useEffect} from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

const dirent = (name: string, directory: boolean) => ({
    name,
    isDirectory: () => directory,
});

const readdir = jest.fn(async (dir: string, opts?: any) => {
    if (opts?.withFileTypes) {
        if (dir === '/mods') return [dirent('web', true), dirent('cloud', true), dirent('README.md', false)];
        if (dir === '/mods/web') return [dirent('module.yaml', false), dirent('tools', true)];
        if (dir === '/mods/cloud') return [dirent('module.yml', false), dirent('tools', true)];
        if (dir.endsWith('/tools')) return [dirent('scan.py', false), dirent('notes.txt', false)];
        return [];
    }
    if (dir === '/mods/web/tools') return ['scan.py', 'notes.txt'];
    if (dir === '/mods/cloud/tools') return ['audit.py'];
    return [];
});

const readFile = jest.fn(async (file: string) => {
    if (file.includes('/web/module.yaml')) return 'web yaml';
    if (file.includes('/cloud/module.yml')) return 'cloud yaml';
    throw new Error('missing');
});

jest.unstable_mockModule('fs/promises', () => ({
    readdir,
    readFile,
}));

jest.unstable_mockModule('js-yaml', () => ({
    load: (content: string) => {
        if (content.includes('cloud')) {
            return {
                description: 'Cloud posture and AWS checks',
                category: 'cloud',
                capabilities: ['aws', 'iam'],
                tools: {audit: {description: 'Audit cloud', category: 'cloud'}},
                report_format: 'cloud-report',
            };
        }
        return {
            description: 'Web application testing',
            category: 'web',
            capabilities: ['xss', 'sql injection'],
            tools: {scan: {description: 'Scan web', category: 'web'}},
        };
    },
}));

const load = async () => {
    const mod = await import('../../../src/contexts/ModuleContext.js');
    return mod;
};

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('ModuleContext', () => {
    beforeEach(() => {
        process.env.CYBER_MODULE_PATH = '/mods';
        readdir.mockClear();
        readFile.mockClear();
    });

    afterEach(() => {
        delete process.env.CYBER_MODULE_PATH;
    });

    it('loads modules, switches modules, suggests modules, and reports missing modules', async () => {
        const {ModuleProvider, useModule} = await load();
        const snapshots: any[] = [];

        const Consumer = () => {
            const context = useModule();
            useEffect(() => {
                snapshots.push(context);
            });
            return <span>{context.currentModule || 'none'}</span>;
        };

        await act(async () => {
            TestRenderer.create(
                <ModuleProvider>
                    <Consumer/>
                </ModuleProvider>
            );
            await Promise.resolve();
            await Promise.resolve();
        });

        const context = snapshots[snapshots.length - 1];
        expect(Object.keys(context.availableModules)).toEqual(expect.arrayContaining(['web', 'cloud']));
        expect(context.currentModule).toBe('web');
        expect(context.moduleInfo.tools).toEqual(expect.arrayContaining([
            expect.objectContaining({name: 'scan', description: 'Scan web'}),
        ]));
        expect(context.suggestModuleForObjective('need aws iam review')).toBe('cloud');
        expect(context.suggestModuleForObjective('sql injection')).toBe('web');

        await act(async () => {
            await context.switchModule('cloud');
        });
        expect(snapshots[snapshots.length - 1].currentModule).toBe('cloud');

        await act(async () => {
            await snapshots[snapshots.length - 1].switchModule('missing');
        });
        expect(snapshots[snapshots.length - 1].error).toContain('Module missing not found');
    });

    it('throws when useModule is used outside provider', async () => {
        const {useModule} = await load();
        const Consumer = () => {
            useModule();
            return <span/>;
        };

        expect(() => {
            act(() => {
                TestRenderer.create(<Consumer/>);
            });
        }).toThrow('useModule must be used within ModuleProvider');
    });
});
