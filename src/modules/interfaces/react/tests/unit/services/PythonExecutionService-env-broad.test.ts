import {EventEmitter} from 'events';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';

const existsSync = jest.fn((file: string) => !String(file).includes('mem0.faiss'));
const execFileSync = jest.fn(() => Buffer.from('Python 3.12.0'));
const spawn = jest.fn((_cmd: string, _args: string[], opts: any) => {
    const proc: any = new EventEmitter();
    proc.stdout = new EventEmitter();
    proc.stderr = new EventEmitter();
    proc.stdin = {write: jest.fn((_input: string, cb?: (err?: Error) => void) => cb?.())};
    proc.kill = jest.fn();
    proc.pid = 4321;
    proc.killed = false;
    proc.__opts = opts;
    setTimeout(() => proc.emit('exit', 0), 0);
    return proc;
});

jest.unstable_mockModule('fs', () => ({
    existsSync,
}));

jest.unstable_mockModule('child_process', () => ({
    exec: jest.fn(),
    spawn,
    execFileSync,
}));

const load = async () => import('../../../src/services/PythonExecutionService.js');

describe('PythonExecutionService broad environment construction', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        jest.resetModules();
        existsSync.mockClear();
        execFileSync.mockClear();
        spawn.mockClear();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('passes CLI args and broad config-derived environment into the Python process', async () => {
        const {PythonExecutionService} = await load();
        const service = new PythonExecutionService();
        (service as any).preflightChecks = jest.fn(async () => true);
        const events: any[] = [];
        service.on('event', event => events.push(event));

        const promise = service.executeAssessment({
            module: 'api_scan',
            target: 'example.com',
            objective: 'audit api',
            continueOperation: true,
            reportOnly: 'report-1',
        } as any, {
            iterations: 3,
            modelProvider: 'litellm',
            modelId: 'gpt-test',
            awsRegion: 'us-west-1',
            confirmations: true,
            verbose: false,
            outputDir: 'outputs',
            environment: {extra_env: 'extra'},
            awsAccessKeyId: 'AKIA',
            awsSecretAccessKey: 'SECRET',
            awsSessionToken: 'SESSION',
            awsBearerToken: 'BEARER',
            awsProfile: 'profile',
            awsRoleArn: 'role',
            awsSessionName: 'session',
            awsWebIdentityTokenFile: '/tmp/token',
            awsStsEndpoint: 'https://sts.example',
            awsExternalId: 'external',
            sagemakerBaseUrl: 'https://sagemaker.example',
            ollamaHost: 'http://ollama',
            ollamaContextLength: 4096,
            ollamaTimeout: 30,
            ollamaKeepAlive: '10m',
            openaiApiKey: 'openai',
            anthropicApiKey: 'anthropic',
            geminiApiKey: 'gemini',
            xaiApiKey: 'xai',
            cohereApiKey: 'cohere',
            azureApiKey: 'azure',
            azureApiBase: 'https://azure.example',
            azureApiVersion: '2026-01-01',
            embeddingModel: 'embed',
            maxTokens: 1000,
            temperature: 0,
            topP: 0.8,
            thinkingBudget: 500,
            reasoningEffort: 'medium',
            reasoningVerbosity: 'high',
            maxCompletionTokens: 1200,
            swarmModel: 'swarm',
            evaluationModel: 'judge',
            memoryModel: 'memory',
            rateLimitTokensPerMinute: 100,
            rateLimitRequestsPerMinute: 10,
            rateLimitConcurrency: 2,
            bugBountyHeaders: {X: 'Y'},
            conversationWindow: 20,
            conversationPreserveFirst: 1,
            conversationPreserveLast: 3,
            toolMaxResultChars: 400,
            toolArtifactThreshold: 800,
            observability: true,
            autoEvaluation: true,
            enableLangfusePrompts: true,
            langfuseHost: 'https://langfuse.example',
            langfusePublicKey: 'public',
            langfuseSecretKey: 'secret',
            langfusePromptLabel: 'staging',
            langfusePromptCacheTTL: 60,
            evaluationBatchSize: 7,
            minToolCalls: 4,
            minEvidence: 2,
            evalMaxWaitSecs: 90,
            evalPollIntervalSecs: 5,
            evalSummaryMaxChars: 9000,
            modelPricing: {
                'gpt-test': {
                    inputCostPer1k: 3,
                    outputCostPer1k: 9,
                    cacheReadCostPer1k: 1,
                    cacheWriteCostPer1k: 2,
                },
            },
        } as any);

        await actPromiseTick();
        await promise;

        expect(spawn).toHaveBeenCalledTimes(1);
        const [, args, opts] = spawn.mock.calls[0] as any;
        expect(args).toEqual(expect.arrayContaining([
            '--module', 'api_scan',
            '--target', 'example.com',
            '--continue',
            '--report', 'report-1',
            '--model', 'gpt-test',
            '--region', 'us-west-1',
        ]));
        expect(opts.detached).toBe(true);
        expect(opts.env.CYBER_OBJECTIVE).toBe('audit api');
        expect(opts.env.BYPASS_TOOL_CONSENT).toBe('false');
        expect(opts.env.AWS_ACCESS_KEY_ID).toBe('AKIA');
        expect(opts.env.AWS_BEARER_TOKEN_BEDROCK).toBe('BEARER');
        expect(opts.env.OLLAMA_CONTEXT_LENGTH).toBe('4096');
        expect(opts.env.OPENAI_API_KEY).toBe('openai');
        expect(opts.env.AZURE_OPENAI_ENDPOINT).toBe('https://azure.example');
        expect(opts.env.CYBER_AGENT_SWARM_MODEL).toBe('swarm');
        expect(opts.env.CYBER_RATE_LIMIT_MAX_CONCURRENT).toBe('2');
        expect(opts.env.CYBER_BUG_BOUNTY_HEADERS).toBe(JSON.stringify({X: 'Y'}));
        expect(opts.env.CYBER_CONVERSATION_WINDOW).toBe('20');
        expect(opts.env.ENABLE_OBSERVABILITY).toBe('true');
        expect(opts.env.LANGFUSE_HOST).toBe('https://langfuse.example');
        expect(opts.env.EVALUATION_BATCH_SIZE).toBe('7');
        expect(opts.env.EVAL_SUMMARY_MAX_CHARS).toBe('9000');
        expect(opts.env.CYBER_AGENT_PRICING_OUTPUT).toBe('0.009');
        expect(opts.env.EXTRA_ENV).toBe('extra');
        expect(events.some(event => event.type === 'operation_complete')).toBe(true);
    });
});

async function actPromiseTick() {
    await Promise.resolve();
    jest.runOnlyPendingTimers();
    await Promise.resolve();
}
