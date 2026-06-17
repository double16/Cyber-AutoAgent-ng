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
    useInput: (handler: (input: string, key: any) => void) => {
        inputHandlers.push(handler);
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
        for (const handler of inputHandlers.slice(-2)) {
            handler(input, key);
        }
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
});
