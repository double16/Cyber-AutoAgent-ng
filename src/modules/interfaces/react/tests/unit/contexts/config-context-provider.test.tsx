import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const mkdir = jest.fn(async () => undefined);
const writeFile = jest.fn(async () => undefined);
const readFile = jest.fn<() => Promise<string>>();
const homedir = jest.fn(() => '/tmp/home');
const clearCache = jest.fn();
const detectDeployments = jest.fn(async () => ({
    availableDeployments: [{mode: 'local-cli', isHealthy: false}],
}));
const loggingService = {
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
};

jest.unstable_mockModule('fs/promises', () => ({
    mkdir,
    writeFile,
    readFile,
}));

jest.unstable_mockModule('os', () => ({
    homedir,
}));

jest.unstable_mockModule('../../../src/services/DeploymentDetector.js', () => ({
    DeploymentDetector: {
        getInstance: () => ({clearCache, detectDeployments}),
    },
}));

jest.unstable_mockModule('../../../src/services/LoggingService.js', () => ({
    loggingService,
}));

const load = async () => import('../../../src/contexts/ConfigContext.js');

describe('ConfigProvider', () => {
    const originalConfigDir = process.env.CYBER_CONFIG_DIR;

    beforeEach(() => {
        process.env.CYBER_CONFIG_DIR = '/tmp/cyber-config-test';
        mkdir.mockClear();
        writeFile.mockClear();
        readFile.mockReset();
        clearCache.mockClear();
        detectDeployments.mockClear();
        Object.values(loggingService).forEach(mock => mock.mockClear());
    });

    afterEach(() => {
        if (originalConfigDir === undefined) {
            delete process.env.CYBER_CONFIG_DIR;
        } else {
            process.env.CYBER_CONFIG_DIR = originalConfigDir;
        }
    });

    it('loads, deep-merges, updates, saves, reloads, and resets configuration', async () => {
        readFile.mockResolvedValueOnce(JSON.stringify({
            deploymentMode: 'single-container',
            isConfigured: true,
            confirmations: true,
            autoApprove: false,
            reportSettings: {
                includeEvidence: false,
            },
            environment: {
                nested: {
                    TOKEN: 'abc',
                },
            },
        }));

        const {ConfigProvider, useConfig, defaultConfig} = await load();
        let ctx: any;
        const Consumer = () => {
            ctx = useConfig();
            return <span>{ctx.config.deploymentMode}:{String(ctx.isConfigLoading)}</span>;
        };

        let view!: TestRenderer.ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(
                <ConfigProvider>
                    <Consumer/>
                </ConfigProvider>
            );
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(clearCache).toHaveBeenCalled();
        expect(detectDeployments).toHaveBeenCalledWith(expect.objectContaining({deploymentMode: 'single-container'}));
        expect(ctx.isConfigLoading).toBe(false);
        expect(ctx.config.deploymentMode).toBe('single-container');
        expect(ctx.config.reportSettings.includeEvidence).toBe(false);
        expect(ctx.config.reportSettings.includeCWE).toBe(defaultConfig.reportSettings.includeCWE);

        act(() => {
            ctx.updateConfig({
                reportSettings: {
                    includeTimestamps: false,
                    includeEvidence: undefined,
                },
                modelProvider: 'ollama',
            });
        });
        expect(ctx.config.modelProvider).toBe('ollama');
        expect(ctx.config.reportSettings.includeTimestamps).toBe(false);
        expect(ctx.config.reportSettings.includeEvidence).toBe(false);

        await act(async () => {
            await ctx.saveConfig();
        });
        expect(mkdir).toHaveBeenCalledWith('/tmp/cyber-config-test', {recursive: true});
        expect(writeFile).toHaveBeenCalledWith(
            '/tmp/cyber-config-test/config.json',
            expect.stringContaining('"modelProvider": "ollama"')
        );

        readFile.mockResolvedValueOnce(JSON.stringify({
            deploymentMode: 'local-cli',
            awsBearerToken: '',
            awsAccessKeyId: '',
            awsSecretAccessKey: '',
        }));
        await act(async () => {
            await ctx.loadConfig();
        });
        expect(ctx.config.deploymentMode).toBe('local-cli');
        expect(ctx.config.observability).toBe(false);
        expect(ctx.config.autoEvaluation).toBe(false);
        expect(ctx.config.awsBearerToken).toBe('');

        act(() => {
            ctx.resetToDefaults();
        });
        expect(ctx.config.deploymentMode).toBe(defaultConfig.deploymentMode);
        view.unmount();
    });

    it('falls back to default config outside a provider', async () => {
        const {useOptionalConfig, defaultConfig} = await load();
        let ctx: any;
        const Consumer = () => {
            ctx = useOptionalConfig();
            return <span>{ctx.config.deploymentMode}</span>;
        };
        act(() => {
            TestRenderer.create(<Consumer/>);
        });

        expect(ctx.config).toBe(defaultConfig);
        await expect(ctx.saveConfig()).resolves.toBeUndefined();
        ctx.updateConfig({modelProvider: 'ollama'});
    });
});
