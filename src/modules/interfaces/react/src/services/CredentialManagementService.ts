import { execFile } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';

import { Config } from '../contexts/ConfigContext.js';

const execFileAsync = promisify(execFile);

export interface CredentialHistoryEvent {
  status: string;
  actor: string;
  reason: string;
  created_at: string;
}

export interface CredentialRecord {
  credential_id: string;
  target?: string;
  role?: string;
  credential_type: string;
  origin: string;
  management_policy: string;
  status: string;
  operation_id?: string;
  created_at: string;
  updated_at: string;
  history: CredentialHistoryEvent[];
  rotation_requests: CredentialRotationRequest[];
}

export interface CredentialRotationRequest {
  request_id: string;
  credential_id: string;
  maintenance_operation_id: string;
  status: string;
  reason: string;
  created_at: string;
}

export interface CredentialInventory {
  credentials: CredentialRecord[];
}

/** Invoke the secret-safe Python management command in the same output mount as operations. */
export class CredentialManagementService {
  async list(target: string, config: Config): Promise<CredentialInventory> {
    return this.run(target, config, ['list']) as Promise<CredentialInventory>;
  }

  async queueRotation(target: string, credentialId: string, reason: string, config: Config): Promise<{
    request: CredentialRotationRequest;
    maintenance_objective: string;
  }> {
    return this.run(target, config, [
      'queue-rotation', '--credential-id', credentialId, '--reason', reason,
    ]) as Promise<{ request: CredentialRotationRequest; maintenance_objective: string }>;
  }

  private async run(target: string, config: Config, command: string[]): Promise<unknown> {
    const outputDir = path.resolve(config.outputDir || './outputs');
    const args = ['-m', 'modules.tools.credential_management', '--output-dir', outputDir, '--target', target, ...command];
    const dockerMode = config.executionMode === 'docker-single' || config.executionMode === 'docker-stack';
    const result = dockerMode
      ? await execFileAsync('docker', [
        'run', '--rm', '-v', `${outputDir}:/app/outputs`, config.dockerImage || 'cyber-autoagent:latest',
        'python', '-m', 'modules.tools.credential_management', '--output-dir', '/app/outputs', '--target', target,
        ...command,
      ])
      : await execFileAsync(path.join(process.cwd(), '.venv', 'bin', 'python'), args, { cwd: process.cwd() });
    const output = (typeof result === 'string' ? result : result.stdout).trim();
    if (!output) throw new Error('Credential manager returned no data');
    return JSON.parse(output);
  }
}
