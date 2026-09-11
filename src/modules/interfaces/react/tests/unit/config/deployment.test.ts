import {afterEach, describe, expect, it, jest} from '@jest/globals';
import {EventEmitter} from 'events';

const originalEnv = {...process.env};
const originalPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
const originalFetch = Object.getOwnPropertyDescriptor(globalThis, 'fetch');

const setPlatform = (platform: NodeJS.Platform) => {
    Object.defineProperty(process, 'platform', {
        value: platform,
        configurable: true,
    });
};

describe('deployment detection config', () => {
    afterEach(() => {
        process.env = {...originalEnv};
        if (originalPlatform) {
            Object.defineProperty(process, 'platform', originalPlatform);
        }
        if (originalFetch) {
            Object.defineProperty(globalThis, 'fetch', originalFetch);
        } else {
            delete (globalThis as any).fetch;
        }
        jest.restoreAllMocks();
        jest.resetModules();
    });

    it('returns CLI defaults outside docker', async () => {
        setPlatform('darwin');
        delete process.env.CONTAINER;
        delete process.env.IS_DOCKER;

        const {getDeploymentDefaults} = await import('../../../src/config/deployment.js');

        expect(getDeploymentDefaults()).toEqual(expect.objectContaining({
            observabilityDefault: false,
            evaluationDefault: false,
            langfuseHost: 'http://localhost:3000',
            description: 'CLI deployment',
        }));
    });

    it('returns container defaults when docker markers are present', async () => {
        setPlatform('darwin');
        process.env.CONTAINER = 'docker';

        const {getDeploymentDefaults} = await import('../../../src/config/deployment.js');

        expect(getDeploymentDefaults()).toEqual(expect.objectContaining({
            langfuseHost: 'http://langfuse-web:3000',
            description: 'Container deployment',
        }));
    });

    it('detects pure CLI mode when local Langfuse health check fails', async () => {
        setPlatform('darwin');
        Object.defineProperty(globalThis, 'fetch', {
            value: jest.fn().mockRejectedValue(new Error('offline')),
            configurable: true,
        });
        const fetchSpy = globalThis.fetch as jest.Mock;

        const {detectDeploymentMode} = await import('../../../src/config/deployment.js');

        await expect(detectDeploymentMode()).resolves.toEqual(expect.objectContaining({
            mode: 'cli',
            observabilityDefault: false,
            evaluationDefault: false,
            langfuseHost: 'http://localhost:3000',
            executionMode: 'local-cli',
        }));
        expect(fetchSpy).toHaveBeenCalledWith('http://localhost:3000/api/public/health', expect.objectContaining({
            method: 'GET',
        }));
    });

    it('detects local compose mode when Langfuse health check succeeds', async () => {
        setPlatform('darwin');
        Object.defineProperty(globalThis, 'fetch', {
            value: jest.fn().mockResolvedValue({ok: true} as Response),
            configurable: true,
        });

        const {detectDeploymentMode} = await import('../../../src/config/deployment.js');

        await expect(detectDeploymentMode()).resolves.toEqual(expect.objectContaining({
            mode: 'compose',
            observabilityDefault: true,
            evaluationDefault: true,
            langfuseHost: 'http://localhost:3000',
            description: 'Local development with observability services running',
        }));
    });

    it('detects single-container mode when docker socket probe fails', async () => {
        setPlatform('darwin');
        process.env.IS_DOCKER = 'true';

        const {Socket} = await import('node:net');
        jest.spyOn(Socket.prototype, 'connect').mockImplementation(function connect(this: EventEmitter) {
            setTimeout(() => this.emit('error', new Error('unreachable')), 0);
            return this as any;
        });
        jest.spyOn(Socket.prototype, 'setTimeout').mockReturnThis();
        jest.spyOn(Socket.prototype, 'destroy').mockImplementation(() => {
        });

        const {detectDeploymentMode} = await import('../../../src/config/deployment.js');

        await expect(detectDeploymentMode()).resolves.toEqual(expect.objectContaining({
            mode: 'container',
            observabilityDefault: false,
            evaluationDefault: false,
            langfuseHost: 'http://localhost:3000',
            description: 'Single container deployment (no observability infrastructure)',
        }));
    });
});
