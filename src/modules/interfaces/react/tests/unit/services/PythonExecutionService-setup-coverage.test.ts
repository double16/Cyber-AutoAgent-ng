import {beforeEach, describe, expect, it, jest} from '@jest/globals';
import {promisify} from 'util';

let existingPaths = new Set<string>();
let commandResults: Array<{match: RegExp | string; stdout?: string; stderr?: string; error?: Error}> = [];
let executedCommands: string[] = [];

const existsSync = jest.fn((file: string) => existingPaths.has(file));
const exec = jest.fn((cmd: string, optsOrCb: any, maybeCb?: any) => {
    executedCommands.push(cmd);
    const cb = typeof optsOrCb === 'function' ? optsOrCb : maybeCb;
    const found = commandResults.find(item =>
        typeof item.match === 'string' ? cmd === item.match : item.match.test(cmd)
    );
    if (found?.error || !found) {
        cb(found?.error || new Error(`missing command mock: ${cmd}`), found?.stdout || '', found?.stderr || '');
        return;
    }
    cb(null, found.stdout || '', found.stderr || '');
});
(exec as any)[promisify.custom] = async (cmd: string) => {
    executedCommands.push(cmd);
    const found = commandResults.find(item =>
        typeof item.match === 'string' ? cmd === item.match : item.match.test(cmd)
    );
    if (found?.error || !found) {
        throw found?.error || new Error(`missing command mock: ${cmd}`);
    }
    return {stdout: found.stdout || '', stderr: found.stderr || ''};
};

jest.unstable_mockModule('fs', () => ({
    existsSync,
}));

jest.unstable_mockModule('child_process', () => ({
    exec,
    spawn: jest.fn(),
    execFileSync: jest.fn(() => Buffer.from('Python 3.12.0')),
}));

const load = async () => import('../../../src/services/PythonExecutionService.js');

describe('PythonExecutionService setup coverage', () => {
    const originalProjectRoot = process.env.CYBER_PROJECT_ROOT;
    const originalPython = process.env.CYBER_PYTHON;
    const originalHome = process.env.HOME;

    beforeEach(() => {
        jest.resetModules();
        existingPaths = new Set(['/project/pyproject.toml']);
        commandResults = [];
        executedCommands = [];
        existsSync.mockClear();
        process.env.CYBER_PROJECT_ROOT = '/project';
        process.env.HOME = '/home/tester';
        delete process.env.CYBER_PYTHON;
    });

    afterEach(() => {
        if (originalProjectRoot === undefined) delete process.env.CYBER_PROJECT_ROOT;
        else process.env.CYBER_PROJECT_ROOT = originalProjectRoot;
        if (originalPython === undefined) delete process.env.CYBER_PYTHON;
        else process.env.CYBER_PYTHON = originalPython;
        if (originalHome === undefined) delete process.env.HOME;
        else process.env.HOME = originalHome;
    });

    it('detects the highest eligible Python interpreter and exposes service metadata', async () => {
        const {PythonExecutionService} = await load();
        commandResults = [
            {match: /^python3\.12 --version/, stdout: 'Python 3.12.2\n'},
            {match: /^python3\.11 --version/, stdout: 'Python 3.11.9\n'},
            {match: /^python3\.10 --version/, stdout: 'Python 3.10.14\n'},
        ];
        const service = new PythonExecutionService();

        await expect(service.checkPythonVersion()).resolves.toEqual({installed: true, version: 'Python 3.12.2'});
        expect(service.getCurrentPythonCommand()).toBe('python3.12');
        expect(service.getActiveProcessPid()).toBeUndefined();
        expect(service.getSessionId()).toMatch(/^py-/);
        expect(service.isActive()).toBe(false);
    });

    it('reports missing Python when no candidate is eligible', async () => {
        const {PythonExecutionService} = await load();
        commandResults = [
            {match: /.*/, error: new Error('not found')},
        ];
        const service = new PythonExecutionService();

        await expect(service.checkPythonVersion()).resolves.toEqual({
            installed: false,
            error: 'Python 3.11+ is required but not found',
        });
    });

    it('checks environment status and emits preflight progress for healthy and unhealthy states', async () => {
        const {PythonExecutionService} = await load();
        existingPaths = new Set([
            '/project/pyproject.toml',
            '/project/.venv',
            '/project/.venv/bin/python',
        ]);
        commandResults = [
            {match: /^python3\.12 --version/, stdout: 'Python 3.12.2\n'},
            {match: /pip" --version/, stdout: 'pip 24\n'},
            {match: /import cyberautoagent/, stdout: ''},
        ];
        const service = new PythonExecutionService();

        const status = await service.checkEnvironmentStatus();
        expect(status).toMatchObject({
            pythonInstalled: true,
            pythonVersion: 'Python 3.12.2',
            venvExists: true,
            venvValid: true,
            dependenciesInstalled: true,
            packageInstalled: true,
            requirementsFile: '/project/pyproject.toml',
        });

        const progress: string[] = [];
        await expect(service.preflightChecks(message => progress.push(message))).resolves.toBe(true);
        expect(progress.join('\n')).toContain('[OK] cyberautoagent import verified');

        existingPaths = new Set(['/project/pyproject.toml']);
        commandResults = [{match: /.*/, error: new Error('not found')}];
        const unhealthy = new PythonExecutionService();
        const unhealthyProgress: string[] = [];
        await expect(unhealthy.preflightChecks(message => unhealthyProgress.push(message))).resolves.toBe(false);
        expect(unhealthyProgress.join('\n')).toContain('[ERR] Python 3.11+ not found');
        expect(unhealthyProgress.join('\n')).toContain('Virtual environment missing');
    });

    it('sets up Python environments across create, recreate, install, verify, and fallback paths', async () => {
        const {PythonExecutionService} = await load();
        existingPaths = new Set(['/project/pyproject.toml']);
        commandResults = [
            {match: /^python3\.12 --version/, stdout: 'Python 3.12.2\n'},
            {match: /python3\.12 -m venv/, stdout: ''},
            {match: /install --upgrade pip/, stdout: ''},
            {match: /install -e \./, stdout: ''},
            {match: /import cyberautoagent; print/, stdout: 'dev\n'},
        ];
        const createService = new PythonExecutionService();
        const createProgress: string[] = [];
        await createService.setupPythonEnvironment(message => createProgress.push(message));
        expect(createProgress.join('\n')).toContain('Virtual environment created');
        expect(executedCommands.some(cmd => cmd.includes('python3.12 -m venv'))).toBe(true);

        existingPaths = new Set(['/project/pyproject.toml', '/project/.venv']);
        commandResults = [
            {match: /^python3\.12 --version/, stdout: 'Python 3.12.2\n'},
            {match: /rm -rf/, stdout: ''},
            {match: /python3\.12 -m venv/, stdout: ''},
            {match: /install --upgrade pip/, stdout: ''},
            {match: /install -e \./, stdout: ''},
            {match: /import cyberautoagent; print/, error: new Error('verify failed')},
        ];
        const invalidService = new PythonExecutionService();
        const invalidProgress: string[] = [];
        await invalidService.setupPythonEnvironment(message => invalidProgress.push(message));
        expect(invalidProgress.join('\n')).toContain('Recreating corrupted virtual environment');
        expect(invalidProgress.join('\n')).toContain('Cyber-AutoAgent installed in development mode');

        existingPaths = new Set(['/project/pyproject.toml', '/project/.venv', '/project/.venv/bin/python']);
        commandResults = [
            {match: /^python3\.12 --version/, stdout: 'Python 3.12.2\n'},
            {match: /pip" --version/, stdout: 'pip 24\n'},
            {match: /"\/project\/\.venv\/bin\/python" -c "import cyberautoagent"/, stdout: ''},
            {match: /"\/project\/\.venv\/bin\/python" --version/, stdout: 'Python 3.9.18\n'},
            {match: /rm -rf/, stdout: ''},
            {match: /python3\.12 -m venv/, stdout: ''},
            {match: /install --upgrade pip/, stdout: ''},
            {match: /install -e \./, stdout: ''},
            {match: /import cyberautoagent; print/, stdout: 'dev\n'},
        ];
        const oldVenvService = new PythonExecutionService();
        const oldProgress: string[] = [];
        await oldVenvService.setupPythonEnvironment(message => oldProgress.push(message));
        expect(oldProgress.join('\n')).toContain('Recreating virtual environment with Python 3.11+');
        expect(oldProgress.join('\n')).toContain('Dependencies already installed');
    });
});
