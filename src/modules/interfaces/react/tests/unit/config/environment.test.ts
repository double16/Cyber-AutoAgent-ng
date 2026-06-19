import * as path from 'path';
import {
  getDockerComposePaths,
  getEnvironment,
  getEnvironmentConfig,
  validateEnvironment,
} from '../../../src/config/environment.js';
import {afterEach, describe, expect, it} from '@jest/globals';

const originalNodeEnv = process.env.NODE_ENV;

describe('environment config', () => {
    afterEach(() => {
        if (originalNodeEnv === undefined) {
            delete process.env.NODE_ENV;
        } else {
            process.env.NODE_ENV = originalNodeEnv;
        }
    });

    it.each([
        ['production', 'production'],
        ['prod', 'production'],
        ['staging', 'staging'],
        ['stage', 'staging'],
        ['development', 'development'],
        ['dev', 'development'],
        ['unexpected', 'development'],
        [undefined, 'development'],
    ] as const)('maps NODE_ENV=%s to %s', (value, expected) => {
        if (value === undefined) {
            delete process.env.NODE_ENV;
        } else {
            process.env.NODE_ENV = value;
        }

        expect(getEnvironment()).toBe(expected);
    });

    it('builds production-specific configuration', () => {
        process.env.NODE_ENV = 'production';

        expect(getEnvironmentConfig()).toEqual(expect.objectContaining({
            env: 'production',
            isProduction: true,
            isDevelopment: false,
            dockerCompose: {file: 'docker-compose.prod.yml', profile: undefined},
            logging: {level: 'info', structured: true},
            docker: expect.objectContaining({
                networkName: 'cyber-autoagent-prod',
                autoCleanup: true,
                healthCheckInterval: 30000,
            }),
            api: {timeout: 60000, retries: 3},
        }));
    });

    it('builds development defaults', () => {
        process.env.NODE_ENV = 'development';

        expect(getEnvironmentConfig()).toEqual(expect.objectContaining({
            env: 'development',
            isDevelopment: true,
            isProduction: false,
            dockerCompose: {file: 'docker-compose.yml', profile: 'dev'},
            logging: {level: 'debug', structured: false},
            docker: expect.objectContaining({
                networkName: 'cyber-autoagent_default',
                autoCleanup: false,
                healthCheckInterval: 3000,
            }),
            api: {timeout: 30000, retries: 1},
        }));
    });

    it('validates required production variables', () => {
        process.env.NODE_ENV = 'production';
        expect(validateEnvironment()).toEqual({valid: true, errors: []});

        delete process.env.NODE_ENV;
        expect(validateEnvironment()).toEqual({valid: true, errors: []});
    });

    it('returns compose paths in preferred fallback order', () => {
        process.env.NODE_ENV = 'production';
        const cwd = process.cwd();

        expect(getDockerComposePaths()).toEqual([
            path.join(cwd, 'docker', 'docker-compose.prod.yml'),
            path.join(cwd, 'docker-compose.prod.yml'),
            path.join(cwd, '..', 'docker', 'docker-compose.prod.yml'),
            path.join(cwd, 'docker', 'docker-compose.yml'),
            path.join(cwd, 'docker-compose.yml'),
        ]);
    });
});
