import {EventEmitter} from 'events';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';
import {promisify} from 'util';

let commandResponses: Array<{match: RegExp | string; stdout?: string; stderr?: string; error?: Error}> = [];
let executedCommands: string[] = [];
const exec = jest.fn((cmd: string, optsOrCb: any, maybeCb?: any) => {
    executedCommands.push(cmd);
    const cb = typeof optsOrCb === 'function' ? optsOrCb : maybeCb;
    const found = commandResponses.find(item =>
        typeof item.match === 'string' ? cmd === item.match : item.match.test(cmd)
    );
    if (found?.error) {
        cb(found.error, found.stdout || '', found.stderr || '');
        return;
    }
    cb(null, found?.stdout || '', found?.stderr || '');
});
(exec as any)[promisify.custom] = async (cmd: string) => {
    executedCommands.push(cmd);
    const found = commandResponses.find(item =>
        typeof item.match === 'string' ? cmd === item.match : item.match.test(cmd)
    );
    if (found?.error) {
        throw found.error;
    }
    return {stdout: found?.stdout || '', stderr: found?.stderr || ''};
};

const spawn = jest.fn((_cmd: string, _args: string[], _opts: any) => {
    const child: any = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = jest.fn();
    queueMicrotask(() => {
        child.stdout.emit('data', 'Pulling cyber-autoagent done\n');
        child.stderr.emit('data', 'Container cyber-autoagent Started\n');
        child.emit('close', 0);
    });
    return child;
});

let existingPaths = new Set<string>();
const existsSync = jest.fn((file: string) => existingPaths.has(file));

jest.unstable_mockModule('child_process', () => ({
    exec,
    spawn,
}));

jest.unstable_mockModule('fs', () => ({
    existsSync,
}));

const load = async () => {
    const mod = await import('../../../src/services/ContainerManager.js');
    (mod.ContainerManager as any).instance = undefined;
    return mod;
};

describe('ContainerManager coverage', () => {
    const originalProjectRoot = process.env.CYBER_PROJECT_ROOT;
    const originalTimeout = process.env.CYBER_CONTAINER_TIMEOUT;
    const originalInterval = process.env.CYBER_READINESS_INTERVAL;

    beforeEach(() => {
        commandResponses = [];
        existingPaths = new Set<string>();
        exec.mockClear();
        spawn.mockClear();
        existsSync.mockClear();
        executedCommands = [];
        process.env.CYBER_PROJECT_ROOT = '/project';
        process.env.CYBER_CONTAINER_TIMEOUT = '50';
        process.env.CYBER_READINESS_INTERVAL = '1';
    });

    afterEach(() => {
        if (originalProjectRoot === undefined) delete process.env.CYBER_PROJECT_ROOT;
        else process.env.CYBER_PROJECT_ROOT = originalProjectRoot;
        if (originalTimeout === undefined) delete process.env.CYBER_CONTAINER_TIMEOUT;
        else process.env.CYBER_CONTAINER_TIMEOUT = originalTimeout;
        if (originalInterval === undefined) delete process.env.CYBER_READINESS_INTERVAL;
        else process.env.CYBER_READINESS_INTERVAL = originalInterval;
    });

    it('checks container status and classifies running, missing, and restart-needed services', async () => {
        const {ContainerManager} = await load();
        commandResponses = [
            {match: 'docker info', stdout: 'ok'},
            {match: /^docker ps --format/, stdout: [
                'cyber-autoagent\tUp 2 minutes',
                'cyber-langfuse\tUp 2 minutes',
                'unrelated\tUp 1 minute',
            ].join('\n')},
            {match: /^docker ps -a --format/, stdout: [
                'cyber-langfuse-worker\tExited (0)',
                'cyber-langfuse-postgres\tCreated',
                'cyber-langfuse-clickhouse\tUp 2 minutes',
            ].join('\n')},
        ];

        const manager = ContainerManager.getInstance();
        const status = await manager.checkContainerStatus();

        expect(status.dockerAvailable).toBe(true);
        expect(status.runningContainers.map(c => c.name)).toEqual(['cyber-autoagent', 'cyber-langfuse']);
        expect(status.requiredContainers['single-container'].running).toEqual(['cyber-autoagent']);
        expect(status.requiredContainers['full-stack'].needsRestart).toEqual(
            expect.arrayContaining(['langfuse-worker', 'postgres'])
        );
        expect(status.requiredContainers['full-stack'].missing).toEqual(
            expect.arrayContaining(['clickhouse', 'redis', 'minio'])
        );
    });

    it('switches local mode without Docker and stops containers when Docker is available', async () => {
        const {ContainerManager} = await load();
        commandResponses = [
            {match: 'docker info', error: new Error('daemon down')},
        ];
        const manager = ContainerManager.getInstance();
        const progress: string[] = [];
        manager.on('progress', message => progress.push(message));

        await manager.switchToMode('local-cli');
        expect(await manager.getCurrentMode()).toBe('local-cli');
        expect(progress.join('\n')).toContain('Docker not available');

        commandResponses = [
            {match: 'docker info', stdout: 'ok'},
            {match: /^docker ps --format/, stdout: 'cyber-autoagent\tUp 1 minute\ncyber-langfuse\tUp 1 minute'},
            {match: /^docker stop cyber-autoagent/, stdout: ''},
            {match: /^docker stop cyber-langfuse/, stdout: ''},
        ];
        progress.length = 0;
        await manager.switchToMode('local-cli');
        expect(progress.join('\n')).toContain('Stopped all containers');
    });

    it('starts a container mode with compose, networks, image checks, and readiness polling', async () => {
        const {ContainerManager} = await load();
        existingPaths.add('/project/docker/docker-compose.yml');
        commandResponses = [
            {match: 'docker info', stdout: 'ok'},
            {match: /^docker ps --format/, stdout: ''},
            {match: /^docker ps -a --format/, stdout: ''},
            {match: 'docker compose version', stdout: 'Docker Compose version v2'},
            {match: /^docker network ls/, stdout: ''},
            {match: /^docker network create/, stdout: 'created'},
            {match: /^docker images -q cyber-autoagent:latest/, stdout: 'image-id\n'},
        ];
        const manager = ContainerManager.getInstance();
        const progress: string[] = [];
        manager.on('progress', message => progress.push(message));
        manager.on('progressMeta', meta => progress.push(`meta:${meta.phase}`));

        const getRunningContainers = jest.spyOn(manager, 'getRunningContainers');
        getRunningContainers
            .mockResolvedValueOnce([])
            .mockResolvedValueOnce([])
            .mockResolvedValueOnce([{name: 'cyber-autoagent', status: 'Up 1 second'}]);

        await manager.switchToMode('single-container');

        expect(spawn).toHaveBeenCalledWith('docker', expect.arrayContaining(['compose', '-f', '/project/docker/docker-compose.yml', 'up']), {
            cwd: '/project/docker',
        });
        expect(executedCommands).toContain('docker network create cyber-autoagent_default');
        expect(progress.join('\n')).toContain('Successfully switched to single-container mode');
        getRunningContainers.mockResolvedValue([{name: 'cyber-autoagent', status: 'Up 1 second'}]);
        expect(await manager.getContainerCount()).toEqual({running: 1, total: 1});
        getRunningContainers.mockRestore();
    });

    it('exercises deployment helpers and matching rules', async () => {
        const {ContainerManager} = await load();
        const manager = ContainerManager.getInstance() as any;

        expect(manager.getDeploymentConfig('full-stack').services).toContain('postgres');
        expect(manager.isServiceContainerMatch('cyber-langfuse-postgres', 'postgres')).toBe(true);
        expect(manager.isServiceContainerMatch('project_postgres_1', 'postgres')).toBe(true);
        expect(manager.isServiceContainerMatch('project-postgres-1', 'postgres')).toBe(true);
        expect(manager.isServiceContainerMatch('postgres-extra', 'postgres')).toBe(true);
        expect(manager.isServiceContainerMatch('redis-cache', 'postgres')).toBe(false);

        commandResponses = [
            {match: 'docker compose version', error: new Error('missing plugin')},
            {match: 'docker-compose version', stdout: 'legacy'},
        ];
        await expect(manager.resolveComposeCmd()).resolves.toEqual({cmd: 'docker-compose', baseArgs: []});

        delete process.env.CYBER_PROJECT_ROOT;
        existingPaths.add(`${process.cwd()}/docker`);
        existingPaths.add(`${process.cwd()}/docker/docker-compose.yml`);
        expect(manager.getComposePath('docker-compose.yml')).toBe(`${process.cwd()}/docker/docker-compose.yml`);

        commandResponses = [{match: /^docker network ls/, stdout: 'cyber-autoagent_default\n'}];
        await expect(manager.ensureNetworksExist()).resolves.toBeUndefined();

        await expect(manager.getRunningCountForServices([])).resolves.toEqual({running: 0, total: 0});
        manager.cleanup();
    });
});
