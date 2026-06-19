import {EventEmitter} from 'events';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';

jest.unstable_mockModule('dockerode', () => ({
    default: jest.fn(() => ({})),
}));

jest.unstable_mockModule('child_process', () => ({
    exec: jest.fn(),
    spawn: jest.fn(),
    execFile: jest.fn(),
    execSync: jest.fn(() => {
        throw new Error('no docker context in tests');
    }),
}));

const load = async () => import('../../../src/services/DirectDockerService.js');

const createStream = () => {
    const stream = new EventEmitter() as any;
    stream.write = jest.fn();
    stream.destroy = jest.fn();
    return stream;
};

describe('DirectDockerService stop', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('force-kills ad-hoc containers when stopping an active assessment', async () => {
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const container = {
            kill: jest.fn(async () => undefined),
        };
        const stream = createStream();
        const stopped = jest.fn();

        (service as any).isExecutionActive = true;
        (service as any).activeContainer = container;
        (service as any).activeContainerOwner = true;
        (service as any).containerStream = stream;
        service.on('stopped', stopped);

        await service.stop();

        expect(container.kill).toHaveBeenCalledWith('SIGKILL');
        expect(stream.destroy).toHaveBeenCalled();
        expect((service as any).isExecutionActive).toBe(false);
        expect((service as any).activeContainer).toBeUndefined();
        expect((service as any).activeContainerOwner).toBe(false);
        expect(stopped).toHaveBeenCalledTimes(1);
    });

    it('does not kill attached service containers when no ownership is recorded', async () => {
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const container = {
            kill: jest.fn(async () => undefined),
        };
        const stream = createStream();
        const stopped = jest.fn();

        (service as any).isExecutionActive = true;
        (service as any).activeContainer = container;
        (service as any).activeContainerOwner = false;
        (service as any).containerStream = stream;
        service.on('stopped', stopped);

        await service.stop();

        expect(container.kill).not.toHaveBeenCalled();
        expect(stream.destroy).toHaveBeenCalled();
        expect((service as any).isExecutionActive).toBe(false);
        expect((service as any).activeContainer).toBe(container);
        expect((service as any).activeContainerOwner).toBe(false);
        expect(stopped).toHaveBeenCalledTimes(1);
    });

    it('kills docker exec processes inside reused service containers instead of only closing the UI stream', async () => {
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const killerStream = new EventEmitter();
        const killerStart = jest.fn((_opts: any, cb: any) => {
            cb(null, killerStream);
            queueMicrotask(() => killerStream.emit('end'));
        });
        const killerExec = {
            start: killerStart,
        };
        const container = {
            kill: jest.fn(async () => undefined),
            exec: jest.fn(async () => killerExec),
        };
        const activeExec = {};
        const stream = createStream();
        const stopped = jest.fn();

        (service as any).isExecutionActive = true;
        (service as any).activeContainer = container;
        (service as any).activeExec = activeExec;
        (service as any).activeExecRunId = 'cyber-exec-test-run';
        (service as any).containerStream = stream;
        service.on('stopped', stopped);

        await service.stop();

        expect(stream.write).toHaveBeenCalledWith('\x03');
        expect(container.kill).not.toHaveBeenCalled();
        expect(container.exec).toHaveBeenCalledWith(expect.objectContaining({
            Cmd: [
                '/bin/sh',
                '-lc',
                expect.stringContaining('CYBER_EXEC_RUN_ID=$run_id'),
            ],
            Tty: false,
            WorkingDir: '/app',
        }));
        const killScript = container.exec.mock.calls[0][0].Cmd[2];
        expect(killScript).toContain("run_id='cyber-exec-test-run'");
        expect(killScript).toContain('/proc/[0-9]*/environ');
        expect(killScript).toContain('[ "$pid" = "1" ] && continue');
        expect(killScript).toContain('kill -TERM $all');
        expect(killScript).toContain('kill -KILL $all');
        expect(killScript).not.toContain('pkill');
        expect(killerStart).toHaveBeenCalled();
        expect(stream.destroy).toHaveBeenCalled();
        expect((service as any).activeExec).toBeUndefined();
        expect((service as any).activeExecRunId).toBeUndefined();
        expect((service as any).activeContainer).toBe(container);
        expect((service as any).activeContainerOwner).toBe(false);
        expect(stopped).toHaveBeenCalledTimes(1);
    });

    it('does not run broad process-name matching when no exec run id is available', async () => {
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const container = {
            exec: jest.fn(),
        };
        const stream = createStream();

        (service as any).isExecutionActive = true;
        (service as any).activeContainer = container;
        (service as any).activeExec = {};
        (service as any).containerStream = stream;

        await service.stop();

        expect(container.exec).not.toHaveBeenCalled();
        expect(stream.write).toHaveBeenCalledWith('\x03');
    });

    it('tags docker exec assessments with a run id for scoped process termination', async () => {
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const stream = createStream();
        stream.pipe = jest.fn();
        const execStart = jest.fn((_opts: any, cb: any) => cb(null, stream));
        const exec = {start: execStart};
        const container = {
            exec: jest.fn(async () => exec),
        };

        await (service as any).execIntoContainer(container, ['--target', 'example.com'], ['A=B'], 'single-container');

        expect(container.exec).toHaveBeenCalledWith(expect.objectContaining({
            Env: expect.arrayContaining([
                'A=B',
                expect.stringMatching(/^CYBER_EXEC_RUN_ID=cyber-exec-/),
            ]),
        }));
        expect((service as any).activeExecRunId).toMatch(/^cyber-exec-/);
        expect((service as any).activeContainer).toBe(container);
        expect((service as any).activeContainerOwner).toBe(false);
    });

    it('cleanup stops only owned containers', async () => {
        const {DirectDockerService} = await load();
        const ownedService = new DirectDockerService();
        const attachedService = new DirectDockerService();
        const ownedContainer = {
            stop: jest.fn(async () => undefined),
        };
        const attachedContainer = {
            stop: jest.fn(async () => undefined),
        };

        (ownedService as any).activeContainer = ownedContainer;
        (ownedService as any).activeContainerOwner = true;
        (attachedService as any).activeContainer = attachedContainer;
        (attachedService as any).activeContainerOwner = false;

        ownedService.cleanup();
        attachedService.cleanup();
        await Promise.resolve();

        expect(ownedContainer.stop).toHaveBeenCalledTimes(1);
        expect(attachedContainer.stop).not.toHaveBeenCalled();
        expect((ownedService as any).activeContainer).toBeUndefined();
        expect((ownedService as any).activeContainerOwner).toBe(false);
        expect((attachedService as any).activeContainer).toBeUndefined();
        expect((attachedService as any).activeContainerOwner).toBe(false);
    });

    it('stopContainer is a no-op for attached containers', async () => {
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const container = {
            stop: jest.fn(async () => undefined),
        };

        (service as any).activeContainer = container;
        (service as any).activeContainerOwner = false;

        await service.stopContainer();

        expect(container.stop).not.toHaveBeenCalled();
        expect((service as any).activeContainer).toBe(container);
        expect((service as any).activeContainerOwner).toBe(false);
    });
});
