import { describe, expect, it, jest } from '@jest/globals';

const execFile = jest.fn((...args: any[]) => args.at(-1)(null, '{"credentials":[]}', ''));
jest.unstable_mockModule('child_process', () => ({ execFile }));

describe('CredentialManagementService', () => {
  it('uses argument arrays and parses secret-safe command output', async () => {
    const { CredentialManagementService } = await import('../../../src/services/CredentialManagementService.js');
    const service = new CredentialManagementService();
    const config = { outputDir: './outputs', executionMode: 'python-cli', dockerImage: 'fixture-image' } as any;

    await expect(service.list('https://app.example.test', config)).resolves.toEqual({ credentials: [] });
    expect(execFile).toHaveBeenCalledWith(
      expect.stringContaining('.venv'),
      expect.arrayContaining(['-m', 'modules.tools.credential_management', '--target', 'https://app.example.test', 'list']),
      expect.objectContaining({ cwd: expect.any(String) }),
      expect.any(Function),
    );
  });

  it('uses the shared output mount in Docker mode and rejects empty output', async () => {
    execFile.mockImplementationOnce((...args: any[]) => args.at(-1)(null, '', ''));
    const { CredentialManagementService } = await import('../../../src/services/CredentialManagementService.js');
    const service = new CredentialManagementService();
    const config = { outputDir: './outputs', deploymentMode: 'single-container', dockerImage: 'fixture-image' } as any;

    await expect(service.list('https://app.example.test', config)).rejects.toThrow('returned no data');
    expect(execFile).toHaveBeenCalledWith(
      'docker',
      expect.arrayContaining(['run', '--rm', '--entrypoint', 'python', '--workdir', '/app', 'fixture-image', '-m', 'modules.tools.credential_management']),
      expect.any(Function),
    );
  });

  it('queues rotations in the default local mode', async () => {
    const { CredentialManagementService } = await import('../../../src/services/CredentialManagementService.js');
    const service = new CredentialManagementService();
    const config = { outputDir: './outputs' } as any;

    await expect(service.queueRotation('https://app.example.test', 'cred-1', 'expiry', config)).resolves.toEqual({
      credentials: [],
    });
    expect(execFile).toHaveBeenLastCalledWith(
      expect.stringContaining('.venv'),
      expect.arrayContaining(['queue-rotation', '--credential-id', 'cred-1', '--reason', 'expiry']),
      expect.anything(),
      expect.any(Function),
    );
  });

  it('uses default output and Docker image values when they are omitted', async () => {
    const { CredentialManagementService } = await import('../../../src/services/CredentialManagementService.js');
    const service = new CredentialManagementService();

    await expect(service.list('https://app.example.test', { executionMode: 'docker-stack' } as any)).resolves.toEqual({
      credentials: [],
    });
    expect(execFile).toHaveBeenLastCalledWith(
      'docker',
      expect.arrayContaining(['cyber-autoagent:latest']),
      expect.any(Function),
    );
  });

  it('honors canonical local deployment mode over a legacy Docker execution mode and rejects invalid JSON', async () => {
    execFile.mockImplementationOnce((...args: any[]) => args.at(-1)(null, 'not-json', ''));
    const { CredentialManagementService } = await import('../../../src/services/CredentialManagementService.js');
    const service = new CredentialManagementService();

    await expect(service.list('https://app.example.test', {
      deploymentMode: 'local-cli', executionMode: 'docker-stack', outputDir: './outputs',
    } as any)).rejects.toThrow('invalid JSON');
    expect(execFile).toHaveBeenLastCalledWith(expect.stringContaining('.venv'), expect.any(Array), expect.anything(), expect.any(Function));
  });
});

it('forwards rotation lifecycle arguments and explicit Python configuration', async () => {
  const env = {...process.env};
  try {
    process.env.CYBER_PROJECT_ROOT = '/workspace/project'; process.env.CYBER_PYTHON = '/custom/python';
    const {CredentialManagementService} = await import('../../../src/services/CredentialManagementService.js');
    const service = new CredentialManagementService();
    for (const [method, command] of [['cancelRotation', 'cancel-rotation'], ['startRotation', 'start-rotation'], ['failRotation', 'fail-rotation']] as const) {
      if (method === 'startRotation') await service[method]('target', 'request', {} as any);
      else await service[method]('target', 'request', 'reason', {} as any);
      expect(execFile).toHaveBeenLastCalledWith('/custom/python', expect.arrayContaining([command, '--request-id', 'request']), {cwd: '/workspace/project'}, expect.any(Function));
    }
    process.env.CYBER_CREDENTIAL_STORE_KEY = 'test-only-key';
    process.env.CYBER_CREDENTIAL_STORE_PREVIOUS_KEYS = 'test-only-old-key';
    await service.list('target', {deploymentMode: 'full-stack'} as any);
    expect(execFile).toHaveBeenLastCalledWith('docker', expect.arrayContaining(['-e', 'CYBER_CREDENTIAL_STORE_KEY', '-e', 'CYBER_CREDENTIAL_STORE_PREVIOUS_KEYS']), expect.any(Function));
  } finally {process.env = env;}
});
