import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';
import TestRenderer, {act} from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

let config: any;
const updateConfig = jest.fn((updates: any) => {
    config = {...config, ...updates};
});
const saveConfig = jest.fn<() => Promise<void>>(async () => undefined);

const inputHandlers: Array<(input: string, key: any) => void> = [];

jest.unstable_mockModule('ink', () => ({
    Box: ({children}: any) => <div>{children}</div>,
    Text: ({children}: any) => <span>{children}</span>,
    useInput: (handler: (input: string, key: any) => void, options?: {isActive?: boolean}) => {
        if (options?.isActive !== false) {
            inputHandlers.push(handler);
        }
    },
}));

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
    useConfig: () => ({config, updateConfig, saveConfig}),
}));

jest.unstable_mockModule('ink-select-input', () => ({
    default: ({items, onSelect}: any) => (
        <div>
            <span>select:{items?.map((item: any) => item.label).join('|')}</span>
            <button onClick={() => onSelect(items?.[0])}>select-first</button>
            <button onClick={() => onSelect(items?.[1] || items?.[0])}>select-second</button>
            <button onClick={() => onSelect(items?.[2] || items?.[0])}>select-third</button>
        </div>
    ),
}));

jest.unstable_mockModule('ink-text-input', () => ({
    default: ({value, onChange, onSubmit}: any) => (
        <div>
            <span>text-input:{value}</span>
            <button onClick={() => onChange?.('42')}>text-change-number</button>
            <button onClick={() => onChange?.('updated-value')}>text-change</button>
            <button onClick={() => onSubmit?.('updated-value')}>text-submit</button>
        </div>
    ),
    UncontrolledTextInput: ({onSubmit}: any) => (
        <button onClick={() => onSubmit('free-form')}>uncontrolled-submit</button>
    ),
}));

jest.unstable_mockModule('../../../src/components/PasswordInput.js', () => ({
    PasswordInput: ({value, onChange, onSubmit}: any) => (
        <div>
            <span>password:{value ? 'set' : 'empty'}</span>
            <button onClick={() => onChange('secret')}>password-change</button>
            <button onClick={() => onSubmit('secret')}>password-submit</button>
        </div>
    ),
}));

jest.unstable_mockModule('../../../src/components/TokenInput.js', () => ({
    TokenInput: ({value, onChange, onSubmit}: any) => (
        <div>
            <span>token:{value ? 'set' : 'empty'}</span>
            <button onClick={() => onChange('token')}>token-change</button>
            <button onClick={() => onSubmit('token')}>token-submit</button>
        </div>
    ),
}));

const load = async () => {
    const {ConfigEditor} = await import('../../../src/components/ConfigEditor.js');
    return {ConfigEditor};
};

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

const sendInput = (input = '', key: Record<string, boolean> = {}) => {
    act(() => {
        inputHandlers.at(-1)?.(input, key);
    });
};

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('ConfigEditor', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        config = {
            deploymentMode: 'local-cli',
            modelProvider: 'bedrock',
            modelId: 'anthropic.claude-sonnet-4-5',
            memoryBackend: 'FAISS',
            observability: true,
            autoEvaluation: true,
            langfuseHost: '',
            langfusePublicKey: '',
            langfuseSecretKey: '',
            outputFormat: 'markdown',
            mcp: {
                enabled: false,
                connections: [{
                    id: 'conn-1',
                    transport: 'sse',
                    server_url: 'http://localhost:9000',
                    plugins: ['web'],
                    allowedTools: ['scan'],
                    timeoutSeconds: 10,
                }],
            },
        };
        updateConfig.mockClear();
        saveConfig.mockClear();
        inputHandlers.length = 0;
        delete (global as any).__inkInputHandler;
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('renders sections, expands fields, edits values, saves, and handles escape close', async () => {
        const {ConfigEditor} = await load();
        const onClose = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
        await act(async () => {
            view = TestRenderer.create(<ConfigEditor onClose={onClose}/>);
            await Promise.resolve();
        });

        const initial = textFromTree(view.toJSON());
        expect(initial).toContain('Configuration Editor');
        expect(initial).toContain('Models & Credentials');
        expect(updateConfig).toHaveBeenCalledWith({observability: false});
        expect(updateConfig).toHaveBeenCalledWith({autoEvaluation: false});

        sendInput('', {return: true});
        expect(textFromTree(view.toJSON())).toContain('Model Provider');
        expect(textFromTree(view.toJSON())).toContain('Primary Model');

        sendInput('', {return: true});
        expect(textFromTree(view.toJSON())).toContain('text-input:');

        sendInput('', {downArrow: true});
        sendInput('', {return: true});
        act(() => {
            view.root.findAllByType('button').find(button => button.props.children === 'text-change')!.props.onClick();
        });
        sendInput('', {return: true});
        expect(textFromTree(view.toJSON())).toContain('Primary Model');

        sendInput('s', {ctrl: true});
        await act(async () => {
            await Promise.resolve();
            jest.advanceTimersByTime(300);
        });
        expect(saveConfig).toHaveBeenCalled();

        sendInput('', {escape: true});
        sendInput('', {escape: true});
        sendInput('', {escape: true});
        expect(textFromTree(view.toJSON())).toContain('Configuration Editor');
    });

    it('navigates MCP fields and triggers connection actions', async () => {
        const fetchMock = jest.fn(async () => ({
            status: 200,
            ok: true,
            text: async () => 'ok',
        }));
        (globalThis as any).fetch = fetchMock;
        const {ConfigEditor} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(<ConfigEditor onClose={jest.fn()}/>);
            await Promise.resolve();
        });

        sendInput('', {escape: true});
        for (let index = 0; index < 6; index += 1) {
            sendInput('', {downArrow: true});
        }
        sendInput('', {return: true});
        expect(textFromTree(view.toJSON())).toContain('MCP Enabled');

        sendInput('', {return: true});
        expect(updateConfig).toHaveBeenCalledWith(expect.objectContaining({
            mcp: expect.objectContaining({enabled: true}),
        }));

        sendInput('a');
        expect(updateConfig).toHaveBeenCalledWith(expect.objectContaining({
            mcp: expect.objectContaining({
                connections: expect.arrayContaining([expect.objectContaining({id: 'conn-2'})]),
            }),
        }));

        sendInput('d');
        expect(updateConfig).toHaveBeenCalledWith(expect.objectContaining({
            mcp: expect.objectContaining({connections: expect.any(Array)}),
        }));

        for (let index = 0; index < 10; index += 1) {
            sendInput('', {downArrow: true});
        }
        await act(async () => {
            sendInput('', {return: true});
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(fetchMock).toHaveBeenCalledWith(
            'http://localhost:9000',
            expect.objectContaining({method: 'GET'})
        );
        expect(textFromTree(view.toJSON())).toContain('OK (200)');
        delete (globalThis as any).fetch;
    });

    it('renders alternate provider, memory, observability, pricing, and output branches', async () => {
        config = {
            ...config,
            deploymentMode: 'full-stack',
            modelProvider: 'litellm',
            modelId: 'gpt-5-reasoning',
            memoryBackend: 'opensearch',
            observability: true,
            autoEvaluation: true,
            langfuseHost: 'http://localhost:3000',
            langfusePublicKey: 'pk',
            langfuseSecretKey: 'sk',
            opensearchHost: 'http://localhost:9200',
            currentModel: {
                inputCostPer1k: 0.01,
                outputCostPer1k: 0.02,
            },
            outputDir: './outputs',
            unifiedOutput: true,
        };
        inputHandlers.length = 0;
        const {ConfigEditor} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(<ConfigEditor onClose={jest.fn()}/>);
            await Promise.resolve();
        });

        sendInput('', {return: true});
        let output = textFromTree(view.toJSON());
        expect(output).toContain('OpenAI API Key');
        expect(output).toContain('Reasoning Effort');
        expect(output).toContain('Max Completion Tokens');

        sendInput('', {escape: true});
        sendInput('', {downArrow: true});
        sendInput('', {downArrow: true});
        sendInput('', {return: true});
        output = textFromTree(view.toJSON());
        expect(output).toContain('OpenSearch Host');

        sendInput('', {escape: true});
        sendInput('', {downArrow: true});
        sendInput('', {return: true});
        output = textFromTree(view.toJSON());
        expect(output).toContain('Langfuse Host');
        expect(output).toContain('Enable Prompt Management');

        sendInput('', {escape: true});
        sendInput('', {downArrow: true});
        sendInput('', {downArrow: true});
        sendInput('', {return: true});
        output = textFromTree(view.toJSON());
        expect(output).toContain('Current Model - Input Cost');

        sendInput('', {escape: true});
        sendInput('', {downArrow: true});
        sendInput('', {downArrow: true});
        sendInput('', {return: true});
        output = textFromTree(view.toJSON());
        expect(output).toContain('Output Directory');
        expect(output).toContain('Unified Output Structure');
    });

    it('renders configuration status and deployment description branches', async () => {
        const {ConfigEditor} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        config = {
            ...config,
            deploymentMode: 'cli',
            modelProvider: '',
            modelId: '',
            observability: false,
            autoEvaluation: false,
        };
        await act(async () => {
            view = TestRenderer.create(<ConfigEditor onClose={jest.fn()}/>);
            await Promise.resolve();
        });
        expect(textFromTree(view.toJSON())).toContain('No provider selected');
        expect(textFromTree(view.toJSON())).toContain('Python CLI mode');

        act(() => {
            config = {
                ...config,
                deploymentMode: 'container',
                modelProvider: 'bedrock',
                modelId: '',
                awsBearerToken: 'token',
            };
            view.update(<ConfigEditor onClose={jest.fn()}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('No model selected');
        expect(textFromTree(view.toJSON())).toContain('Single container mode');

        act(() => {
            config = {
                ...config,
                deploymentMode: 'compose',
                modelProvider: 'ollama',
                modelId: 'qwen',
                ollamaHost: 'http://localhost:11434',
            };
            view.update(<ConfigEditor onClose={jest.fn()}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('Ready');
        expect(textFromTree(view.toJSON())).toContain('Full stack mode');
    });

    it('initializes missing MCP settings with defaults', async () => {
        config = {
            deploymentMode: 'full-stack',
            modelProvider: 'bedrock',
            modelId: 'claude',
            memoryBackend: 'FAISS',
            observability: false,
            autoEvaluation: false,
        };
        const {ConfigEditor} = await load();

        await act(async () => {
            TestRenderer.create(<ConfigEditor onClose={jest.fn()}/>);
            await Promise.resolve();
        });
        expect(updateConfig).toHaveBeenCalledWith(expect.objectContaining({
            mcp: {enabled: false, connections: []},
        }));
    });

    it('tests MCP fallback endpoints and reports failed diagnostics', async () => {
        config = {
            ...config,
            mcp: {
                enabled: true,
                connections: [{
                    id: 'http-1',
                    transport: 'streamable-http',
                    server_url: 'http://mcp.local',
                    headers: {Authorization: 'Bearer token'},
                    plugins: ['*'],
                    allowedTools: ['*'],
                    timeoutSeconds: 5,
                }],
            },
        };
        const fetchMock = jest
            .fn()
            .mockResolvedValueOnce({status: 404, text: async () => 'missing'})
            .mockResolvedValueOnce({status: 404, text: async () => 'missing stream'})
            .mockResolvedValueOnce({status: 404, text: async () => 'missing post'})
            .mockResolvedValueOnce({status: 500, text: async () => 'server exploded'});
        (globalThis as any).fetch = fetchMock;
        const {ConfigEditor} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(<ConfigEditor onClose={jest.fn()}/>);
            await Promise.resolve();
        });

        for (let index = 0; index < 6; index += 1) {
            sendInput('', {downArrow: true});
        }
        sendInput('', {return: true});
        for (let index = 0; index < 10; index += 1) {
            sendInput('', {downArrow: true});
        }
        await act(async () => {
            sendInput('', {return: true});
            await Promise.resolve();
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(fetchMock).toHaveBeenCalledWith('http://mcp.local/stream', expect.objectContaining({method: 'GET'}));
        expect(fetchMock).toHaveBeenCalledWith('http://mcp.local/mcp', expect.objectContaining({method: 'POST'}));
        expect(textFromTree(view.toJSON())).toContain('FAILED (404)');

        delete (globalThis as any).fetch;
    });

    it('renders MCP plugin and allowed-tool list editors', async () => {
        config = {
            ...config,
            mcp: {
                enabled: true,
                connections: [{
                    id: 'conn-list',
                    transport: 'sse',
                    server_url: 'http://localhost:9000',
                    plugins: ['web'],
                    allowedTools: ['scan'],
                    timeoutSeconds: 10,
                }],
            },
        };
        const {ConfigEditor} = await load();
        let view!: TestRenderer.ReactTestRenderer;

        await act(async () => {
            view = TestRenderer.create(<ConfigEditor onClose={jest.fn()}/>);
            await Promise.resolve();
        });

        for (let index = 0; index < 6; index += 1) {
            sendInput('', {downArrow: true});
        }
        sendInput('', {return: true});

        for (let index = 0; index < 7; index += 1) {
            sendInput('', {downArrow: true});
        }
        sendInput('', {return: true});
        expect(textFromTree(view.toJSON())).toContain('Plugins:');
        act(() => {
            view.root.findAllByType('button').find(button => button.props.children === 'uncontrolled-submit' || button.props.children === 'text-submit')?.props.onClick();
        });

        sendInput('', {escape: true});
        sendInput('', {downArrow: true});
        sendInput('', {return: true});
        expect(textFromTree(view.toJSON())).toContain('Allowed Tools:');
        act(() => {
            view.root.findAllByType('button').find(button => button.props.children === 'text-submit')?.props.onClick();
        });
        expect(textFromTree(view.toJSON())).toContain('Enter = add');
    });
});
