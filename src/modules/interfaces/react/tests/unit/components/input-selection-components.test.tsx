import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {jest} from '@jest/globals';
import TestRenderer, {act} from 'react-test-renderer';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

jest.unstable_mockModule('ink-spinner', () => ({
    default: ({type}: { type?: string }) => <span>spinner:{type}</span>,
}));

const switchModule = jest.fn<() => Promise<void>>(async () => undefined);
let moduleState = {
    availableModules: {
        recon: {description: 'Reconnaissance checks'},
        web: {description: 'Web application tests'},
        cloud: {description: 'Cloud posture review'},
    },
    currentModule: 'web',
};

jest.unstable_mockModule('../../../src/contexts/ModuleContext.js', () => ({
    useModule: () => ({
        ...moduleState,
        switchModule,
    }),
}));

const load = async () => {
    const [
        {render},
        {SafetyWarning},
        {RadioSelect},
        {ThinkingIndicator, InlineThinking},
        {ModuleSelector},
    ] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/SafetyWarning.js'),
        import('../../../src/components/shared/RadioSelect.js'),
        import('../../../src/components/ThinkingIndicator.js'),
        import('../../../src/components/ModuleSelector.js'),
    ]);

    return {render, SafetyWarning, RadioSelect, ThinkingIndicator, InlineThinking, ModuleSelector};
};

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

const sendInput = (input = '', key: Record<string, boolean> = {}) => {
    act(() => {
        (global as any).__inkInputHandler?.(input, key);
    });
};

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('input and selection components', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        jest.setSystemTime(new Date('2026-06-17T12:00:00Z'));
        switchModule.mockClear();
        moduleState = {
            availableModules: {
                recon: {description: 'Reconnaissance checks'},
                web: {description: 'Web application tests'},
                cloud: {description: 'Cloud posture review'},
            },
            currentModule: 'web',
        };
        delete process.env.CYBER_TEST_MODE;
        delete (global as any).__inkInputHandler;
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('requires two y confirmations and cancels SafetyWarning with n or escape', async () => {
        const {SafetyWarning} = await load();
        const onConfirm = jest.fn();
        const onCancel = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <SafetyWarning target="example.com" module="web" onConfirm={onConfirm} onCancel={onCancel}/>
            );
        });

        expect(textFromTree(view.toJSON())).toContain('Do you acknowledge');
        sendInput('y');
        expect(onConfirm).not.toHaveBeenCalled();
        expect(textFromTree(view.toJSON())).toContain('Proceed with cyber operation');

        sendInput('y');
        expect(onConfirm).toHaveBeenCalledTimes(1);
        sendInput('y');
        expect(onConfirm).toHaveBeenCalledTimes(1);

        act(() => view.update(<SafetyWarning target="example.org" module="recon" onConfirm={onConfirm}
                                             onCancel={onCancel}/>));
        sendInput('n');
        expect(onCancel).toHaveBeenCalledTimes(1);
        sendInput('', {escape: true});
        expect(onCancel).toHaveBeenCalledTimes(2);
    });

    it('auto-confirms SafetyWarning in test mode', async () => {
        const {SafetyWarning} = await load();
        const onConfirm = jest.fn();
        const consoleSpy = jest.spyOn(console, 'log').mockImplementation(() => undefined);
        process.env.CYBER_TEST_MODE = 'true';

        act(() => {
            TestRenderer.create(
                <SafetyWarning target="10.0.0.1" module="cloud" onConfirm={onConfirm} onCancel={jest.fn()}/>
            );
        });
        expect(onConfirm).not.toHaveBeenCalled();
        act(() => {
            jest.advanceTimersByTime(50);
        });

        expect(onConfirm).toHaveBeenCalledTimes(1);
        expect(consoleSpy).toHaveBeenCalledWith('[TEST_EVENT] safety_auto');
        consoleSpy.mockRestore();
    });

    it('navigates RadioSelect, skips disabled items, and supports number selection', async () => {
        const {RadioSelect} = await load();
        const onSelect = jest.fn();
        const onHighlight = jest.fn();
        const items = [
            {label: 'Alpha', value: 'a', description: 'first'},
            {label: 'Beta', value: 'b', disabled: true, badge: 'locked'},
            {label: 'Gamma', value: 'c', description: 'third', badge: 'ready'},
        ];

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <RadioSelect
                    items={items}
                    onSelect={onSelect}
                    onHighlight={onHighlight}
                    renderBadge={(badge?: string) => <span>badge:{badge}</span>}
                />
            );
        });

        expect(textFromTree(view.toJSON())).toContain('Alpha');
        expect(onHighlight).toHaveBeenLastCalledWith('a');

        sendInput('', {downArrow: true});
        expect(onHighlight).toHaveBeenLastCalledWith('c');

        sendInput('', {return: true});
        expect(onSelect).toHaveBeenCalledWith('c');

        sendInput('1');
        expect(onSelect).toHaveBeenLastCalledWith('a');

        sendInput('2');
        expect(onSelect).not.toHaveBeenLastCalledWith('b');

        act(() => {
            view.update(<RadioSelect items={[]} onSelect={onSelect}/>);
        });
        sendInput('', {downArrow: true});
        expect(onSelect).toHaveBeenCalledTimes(2);
    });

    it('renders ThinkingIndicator timing modes and inline dot animation', async () => {
        const {render, ThinkingIndicator, InlineThinking} = await load();
        const startTime = Date.now() - 65_000;

        expect(render(<ThinkingIndicator context="startup" startTime={startTime} taskTitle="Boot"/>).lastFrame())
            .toContain('Boot - Initializing');
        expect(render(<ThinkingIndicator context="rate_limit" startTime={startTime} enabled={false}/>).lastFrame())
            .toContain('[BUSY]');
        expect(render(<ThinkingIndicator message="Custom message"/>).lastFrame())
            .toContain('Custom message');

        let inline!: TestRenderer.ReactTestRenderer;
        act(() => {
            inline = TestRenderer.create(<InlineThinking message="wait"/>);
        });
        expect(textFromTree(inline.toJSON())).toContain('wait');
        act(() => {
            jest.advanceTimersByTime(400);
        });
        expect(textFromTree(inline.toJSON())).toContain('wait.');
    });

    it('navigates ModuleSelector and closes on current selection or escape', async () => {
        const {ModuleSelector} = await load();
        const onClose = jest.fn();
        const onSelect = jest.fn();

        act(() => {
            TestRenderer.create(<ModuleSelector onClose={onClose} onSelect={onSelect}/>);
        });

        sendInput('', {downArrow: true});
        sendInput('', {return: true});
        await act(async () => {
            await Promise.resolve();
        });
        expect(switchModule).toHaveBeenCalledWith('cloud');
        expect(onSelect).toHaveBeenCalledWith('cloud');
        expect(onClose).toHaveBeenCalledTimes(1);

        onClose.mockClear();
        onSelect.mockClear();
        switchModule.mockClear();
        act(() => {
            TestRenderer.create(<ModuleSelector onClose={onClose} onSelect={onSelect}/>);
        });
        sendInput('', {return: true});
        expect(switchModule).not.toHaveBeenCalled();
        expect(onClose).toHaveBeenCalledTimes(1);

        act(() => {
            TestRenderer.create(<ModuleSelector onClose={onClose}/>);
        });
        sendInput('', {escape: true});
        expect(onClose).toHaveBeenCalledTimes(2);
    });
});
