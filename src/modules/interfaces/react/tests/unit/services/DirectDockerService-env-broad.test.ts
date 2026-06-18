import {EventEmitter} from 'events';
import {beforeEach, describe, expect, it, jest} from '@jest/globals';

const createContainer = jest.fn(async () => {
    throw new Error('abort-after-create');
});
const listContainers = jest.fn(async () => []);
const getContainer = jest.fn();

jest.unstable_mockModule('dockerode', () => ({
    default: jest.fn(() => ({
        createContainer,
        listContainers,
        getContainer,
    })),
}));

jest.unstable_mockModule('child_process', () => ({
    execSync: jest.fn((cmd: string) => {
        if (cmd === 'docker context show') return 'desktop-linux\n';
        if (cmd.startsWith('docker context inspect')) {
            return JSON.stringify([{Endpoints: {docker: {Host: 'unix:///tmp/docker.sock'}}}]);
        }
        return '';
    }),
}));

const currentMode = jest.fn(async () => 'full-stack');

jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({
    ContainerManager: {
        getInstance: () => ({getCurrentMode: currentMode}),
    },
}));

const load = async () => import('../../../src/services/DirectDockerService.js');

const baseConfig = {
    iterations: 9,
    modelProvider: 'litellm',
    modelId: 'gpt-test',
    awsRegion: 'us-east-2',
    outputDir: '/tmp/cyber-outputs',
    dockerImage: 'cyber:test',
    confirmations: false,
    verbose: true,
    environment: {customKey: 'custom-value'},
    mcp: {
        enabled: true,
        connections: [{id: 'tools', transport: 'sse', server_url: 'http://mcp'}],
    },
    bugBountyHeaders: {'X-Bug-Bounty': 'yes'},
    rateLimitTokensPerMinute: 1000,
    rateLimitRequestsPerMinute: 20,
    rateLimitConcurrency: 3,
    awsAccessKeyId: 'AKIA',
    awsSecretAccessKey: 'SECRET',
    awsSessionToken: 'SESSION',
    awsBearerToken: 'BEARER',
    awsProfile: 'profile',
    awsRoleArn: 'arn:aws:iam::123:role/test',
    awsSessionName: 'session',
    awsWebIdentityTokenFile: '/tmp/token',
    awsStsEndpoint: 'https://sts.example',
    awsExternalId: 'external',
    sagemakerBaseUrl: 'https://sagemaker.example',
    ollamaHost: 'http://ollama',
    ollamaContextLength: 4096,
    ollamaTimeout: 30,
    ollamaKeepAlive: '5m',
    openaiApiKey: 'openai',
    anthropicApiKey: 'anthropic',
    geminiApiKey: 'gemini',
    xaiApiKey: 'xai',
    cohereApiKey: 'cohere',
    azureApiKey: 'azure',
    azureApiBase: 'https://azure.example',
    azureApiVersion: '2026-01-01',
    embeddingModel: 'embed',
    maxTokens: 10000,
    temperature: 0.2,
    topP: 0.9,
    thinkingBudget: 1024,
    reasoningEffort: 'high',
    reasoningVerbosity: 'low',
    maxCompletionTokens: 2048,
    swarmModel: 'swarm',
    evaluationModel: 'judge',
    memoryModel: 'memory',
    conversationWindow: 50,
    conversationPreserveFirst: 2,
    conversationPreserveLast: 5,
    toolMaxResultChars: 1000,
    toolArtifactThreshold: 2000,
    observability: true,
    langfuseHost: 'https://langfuse.example',
    langfuseHostOverride: true,
    langfusePublicKey: 'public',
    langfuseSecretKey: 'secret',
    enableLangfusePrompts: true,
    langfusePromptLabel: 'staging',
    langfusePromptCacheTTL: 60,
    autoEvaluation: true,
    evaluationBatchSize: 7,
    minToolCalls: 4,
    minEvidence: 2,
    evalMaxWaitSecs: 90,
    evalPollIntervalSecs: 6,
    evalSummaryMaxChars: 9000,
    modelPricing: {
        'gpt-test': {
            inputCostPer1k: 3,
            outputCostPer1k: 9,
            cacheReadCostPer1k: 1,
            cacheWriteCostPer1k: 2,
        },
    },
} as any;

describe('DirectDockerService broad environment construction', () => {
    beforeEach(() => {
        jest.resetModules();
        createContainer.mockClear();
        listContainers.mockClear();
        currentMode.mockResolvedValue('full-stack');
        delete process.env.CYBER_DOCKER_REUSE;
    });

    it('passes CLI args, mounts, observability, evaluation, provider, AWS, MCP, and user env to Docker', async () => {
        process.env.CYBER_DOCKER_REUSE = 'false';
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const events: any[] = [];
        service.on('event', event => events.push(event));

        await expect(service.executeAssessment({
            module: 'web_scan',
            target: 'https://example.com/a b',
            objective: 'find issues',
            continueOperation: 'op-123',
            reportOnly: true,
        } as any, baseConfig)).rejects.toThrow('abort-after-create');

        expect(createContainer).toHaveBeenCalledTimes(1);
        const spec = createContainer.mock.calls[0][0] as any;
        expect(spec.Image).toBe('cyber:test');
        expect(spec.Cmd).toEqual(expect.arrayContaining([
            '--module', 'web_scan',
            '--target', 'https://example.com/a b',
            '--continue', 'op-123',
            '--report',
            '--model', 'gpt-test',
            '--region', 'us-east-2',
        ]));
        expect(spec.HostConfig.NetworkMode).toBe('bridge');
        expect(spec.HostConfig.Binds).toContain('/tmp/cyber-outputs:/app/outputs');

        const env = Object.fromEntries(spec.Env.map((entry: string) => {
            const idx = entry.indexOf('=');
            return [entry.slice(0, idx), entry.slice(idx + 1)];
        }));
        expect(env.CYBER_OBJECTIVE).toBe('find issues');
        expect(env.BYPASS_TOOL_CONSENT).toBe('true');
        expect(env.CYBER_AGENT_PROVIDER).toBe('litellm');
        expect(env.CYBER_AGENT_LLM_MODEL).toBe('gpt-test');
        expect(env.CYBER_BUG_BOUNTY_HEADERS).toBe(JSON.stringify({'X-Bug-Bounty': 'yes'}));
        expect(env.AWS_ACCESS_KEY_ID).toBe('AKIA');
        expect(env.AWS_SECRET_ACCESS_KEY).toBe('SECRET');
        expect(env.AWS_BEARER_TOKEN_BEDROCK).toBe('BEARER');
        expect(env.AWS_DEFAULT_REGION).toBe('us-east-2');
        expect(env.OLLAMA_HOST).toBe('http://ollama');
        expect(env.OPENAI_API_KEY).toBe('openai');
        expect(env.AZURE_OPENAI_ENDPOINT).toBe('https://azure.example');
        expect(env.CYBER_AGENT_SWARM_MODEL).toBe('swarm');
        expect(env.CYBER_MCP_ENABLED).toBe('true');
        expect(env.ENABLE_OBSERVABILITY).toBe('true');
        expect(env.LANGFUSE_HOST).toBe('https://langfuse.example');
        expect(env.ENABLE_AUTO_EVALUATION).toBe('true');
        expect(env.EVAL_SUMMARY_MAX_CHARS).toBe('9000');
        expect(env.CYBER_AGENT_PRICING_INPUT).toBe('0.003');
        expect(env.CUSTOMKEY).toBe('custom-value');
        expect(events.map(event => event.content).join('\n')).toContain('Using configured Langfuse host');
    });

    it('emits disabled observability status for non-observable runs', async () => {
        process.env.CYBER_DOCKER_REUSE = 'false';
        currentMode.mockResolvedValue('single-container');
        const {DirectDockerService} = await load();
        const service = new DirectDockerService();
        const events: any[] = [];
        service.on('event', event => events.push(event));

        await expect(service.executeAssessment(
            {module: 'web', target: 'example.com'} as any,
            {...baseConfig, observability: false, autoEvaluation: false, environment: {}, mcp: {enabled: false, connections: []}} as any
        )).rejects.toThrow('abort-after-create');

        const env = Object.fromEntries((createContainer.mock.calls[0][0] as any).Env.map((entry: string) => {
            const idx = entry.indexOf('=');
            return [entry.slice(0, idx), entry.slice(idx + 1)];
        }));
        expect(env.ENABLE_OBSERVABILITY).toBe('false');
        expect(env.ENABLE_AUTO_EVALUATION).toBe('false');
        expect(events.map(event => event.content).join('\n')).toContain('Disabled by user configuration in single-container mode');
    });
});
