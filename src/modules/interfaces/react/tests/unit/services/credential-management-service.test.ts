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
    const config = { outputDir: './outputs', executionMode: 'docker-single', dockerImage: 'fixture-image' } as any;

    await expect(service.list('https://app.example.test', config)).rejects.toThrow('returned no data');
    expect(execFile).toHaveBeenCalledWith(
      'docker',
      expect.arrayContaining(['run', '--rm', 'fixture-image', 'python', '-m', 'modules.tools.credential_management']),
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
});
