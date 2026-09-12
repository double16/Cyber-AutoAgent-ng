import { execFile } from 'child_process';
import * as fs from 'fs';
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
  account_label?: string;
  tenant_label?: string;
  invalidated_at?: string;
  supersedes_credential_id?: string;
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
  request_operation_id?: string;
  claimed_task_uid?: string;
  failure_reason?: string;
  evidence_refs?: string[];
  staged_credential_id?: string;
  completed_at?: string;
  updated_at?: string;
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

  async cancelRotation(target: string, requestId: string, reason: string, config: Config): Promise<{
    request: CredentialRotationRequest;
  }> {
    return this.run(target, config, [
      'cancel-rotation', '--request-id', requestId, '--reason', reason,
    ]) as Promise<{ request: CredentialRotationRequest }>;
  }

  async startRotation(target: string, requestId: string, config: Config): Promise<{
    request: CredentialRotationRequest;
    maintenance_objective: string;
  }> {
    return this.run(target, config, ['start-rotation', '--request-id', requestId]) as Promise<{
      request: CredentialRotationRequest;
      maintenance_objective: string;
    }>;
  }

  async failRotation(target: string, requestId: string, reason: string, config: Config): Promise<{
    request: CredentialRotationRequest;
  }> {
    return this.run(target, config, ['fail-rotation', '--request-id', requestId, '--reason', reason]) as Promise<{
      request: CredentialRotationRequest;
    }>;
  }

  private async run(target: string, config: Config, command: string[]): Promise<unknown> {
    const outputDir = path.resolve(config.outputDir || './outputs');
    const args = ['-m', 'modules.tools.credential_management', '--output-dir', outputDir, '--target', target, ...command];
    const dockerMode = this.isDockerMode(config);
    const result = dockerMode
      ? await execFileAsync('docker', this.dockerArgs(outputDir, target, config, command))
      : await execFileAsync(this.pythonExecutable(), args, { cwd: this.projectRoot() });
    const output = (typeof result === 'string' ? result : result.stdout).trim();
    if (!output) throw new Error('Credential manager returned no data');
    try {
      return JSON.parse(output);
    } catch {
      throw new Error('Credential manager returned invalid JSON');
    }
  }

  private isDockerMode(config: Config): boolean {
    // deploymentMode is the canonical UI contract. executionMode is retained only for old saved configurations.
    const mode = config.deploymentMode;
    if (mode) return mode === 'single-container' || mode === 'full-stack';
    return config.executionMode === 'docker-single' || config.executionMode === 'docker-stack';
  }

  private projectRoot(): string {
    const configuredRoot = process.env.CYBER_PROJECT_ROOT?.trim();
    if (configuredRoot) return path.resolve(configuredRoot);
    let current = process.cwd();
    while (path.dirname(current) !== current) {
      if (fs.existsSync(path.join(current, 'pyproject.toml'))) return current;
      current = path.dirname(current);
    }
    return process.cwd();
  }

  private pythonExecutable(): string {
    const configuredPython = process.env.CYBER_PYTHON?.trim();
    if (configuredPython) return configuredPython;
    const binary = process.platform === 'win32' ? 'python.exe' : 'python';
    return path.join(this.projectRoot(), '.venv', process.platform === 'win32' ? 'Scripts' : 'bin', binary);
  }

  private dockerArgs(outputDir: string, target: string, config: Config, command: string[]): string[] {
    const args = [
      'run', '--rm', '--entrypoint', 'python', '--workdir', '/app', '-v', `${outputDir}:/app/outputs`,
    ];
    for (const variable of ['CYBER_CREDENTIAL_STORE_KEY', 'CYBER_CREDENTIAL_STORE_PREVIOUS_KEYS']) {
      if (process.env[variable]) args.push('-e', variable);
    }
    return [
      ...args, config.dockerImage || 'cyber-autoagent:latest', '-m', 'modules.tools.credential_management',
      '--output-dir', '/app/outputs', '--target', target, ...command,
    ];
  }
}
