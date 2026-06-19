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

let moduleContext = {
    currentModule: 'web',
    availableModules: {
        web: {description: 'Web module'},
        cloud: {description: 'Cloud module'},
    },
};

jest.unstable_mockModule('../../../src/contexts/ModuleContext.js', () => ({
    useModule: () => moduleContext,
}));

jest.unstable_mockModule('../../../src/components/MultiLineTextInput.js', () => ({
    MultiLineTextInput: ({value, onChange, onSubmit, placeholder, showCursor, focus}: any) => (
        <div>
            <span>input:{value}</span>
            <span>placeholder:{placeholder}</span>
            <span>cursor:{String(showCursor)}</span>
            <span>focus:{String(focus)}</span>
            <button onClick={() => onChange('target https://testphp.vulnweb.com')}>change-target</button>
            <button onClick={() => onChange('/he')}>change-help</button>
            <button onClick={() => onChange('line one')}>change-line</button>
            <button onClick={() => onSubmit(value)}>submit</button>
        </div>
    ),
}));

const load = async () => {
    const {UnifiedInputPrompt} = await import('../../../src/components/UnifiedInputPrompt.js');
    return {UnifiedInputPrompt};
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

describe('UnifiedInputPrompt', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        moduleContext = {
            currentModule: 'web',
            availableModules: {
                web: {description: 'Web module'},
                cloud: {description: 'Cloud module'},
            },
        };
        delete (global as any).__inkInputHandler;
        (global as any).CYBER_APP_STATE_REF = {
            getCommandHistory: () => ['target old.example', 'execute old objective'],
        };
        (global as any).CYBER_APP_STATE_ACTIONS = {
            pushCommandHistory: jest.fn(),
        };
    });

    afterEach(() => {
        jest.useRealTimers();
        delete (global as any).CYBER_APP_STATE_REF;
        delete (global as any).CYBER_APP_STATE_ACTIONS;
    });

    it('renders prompt variants, suggestions, navigation, history, and submit behavior', async () => {
        const {UnifiedInputPrompt} = await load();
        const onInput = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <UnifiedInputPrompt
                    flowState={{step: 'idle'}}
                    onInput={onInput}
                    recentTargets={['https://recent.example']}
                />
            );
        });

        expect(textFromTree(view.toJSON())).toContain('◆ web >');
        expect(textFromTree(view.toJSON())).toContain('target <url>');

        const buttons = view.root.findAllByType('button');
        act(() => {
            buttons.find(button => button.props.children === 'change-help')!.props.onClick();
        });
        expect(textFromTree(view.toJSON())).toContain('Suggestions:');
        expect(textFromTree(view.toJSON())).toContain('/help');

        sendInput('', {downArrow: true});
        sendInput('', {upArrow: true});
        sendInput('', {tab: true});
        expect(textFromTree(view.toJSON())).toContain('input:/help');

        sendInput('', {escape: true});
        sendInput('', {upArrow: true});
        expect(textFromTree(view.toJSON())).toContain('execute old objective');
        sendInput('', {downArrow: true});

        act(() => {
            buttons.find(button => button.props.children === 'change-line')!.props.onClick();
        });
        sendInput('o', {ctrl: true});
        expect(textFromTree(view.toJSON())).toContain('Hint: Press Ctrl+O');

        act(() => {
            view.root.findAllByType('button').find(button => button.props.children === 'submit')!.props.onClick();
            jest.runOnlyPendingTimers();
        });
        expect(onInput).toHaveBeenCalledWith(expect.stringContaining('line one'));
        expect((global as any).CYBER_APP_STATE_ACTIONS.pushCommandHistory).toHaveBeenCalledWith(expect.stringContaining('line one'));
    });

    it('updates placeholders for flow states and handles disabled and handoff modes', async () => {
        const {UnifiedInputPrompt} = await load();
        const onInput = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <UnifiedInputPrompt flowState={{step: 'target'}} onInput={onInput}/>
            );
        });
        expect(textFromTree(view.toJSON())).toContain('target https://your-authorized-target.com');

        act(() => {
            view.update(<UnifiedInputPrompt flowState={{step: 'objective'}} onInput={onInput}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('execute <your objective>');

        act(() => {
            view.update(<UnifiedInputPrompt flowState={{step: 'ready'}} onInput={onInput}/>);
        });
        expect(textFromTree(view.toJSON())).toContain('Press Enter');

        act(() => {
            view.update(<UnifiedInputPrompt flowState={{step: 'idle'}} onInput={onInput} disabled/>);
        });
        expect(textFromTree(view.toJSON())).toContain('Operation running');
        sendInput('x');
        expect(onInput).not.toHaveBeenCalled();

        act(() => {
            view.update(<UnifiedInputPrompt flowState={{step: 'idle'}} onInput={onInput} disabled userHandoffActive/>);
        });
        expect(textFromTree(view.toJSON())).toContain('response:');
        expect(textFromTree(view.toJSON())).toContain('Enter your response');

        act(() => {
            view.root.findAllByType('button').find(button => button.props.children === 'change-target')!.props.onClick();
        });
        act(() => {
            view.root.findAllByType('button').find(button => button.props.children === 'submit')!.props.onClick();
            jest.runOnlyPendingTimers();
        });
        expect(onInput).toHaveBeenCalledWith('target https://testphp.vulnweb.com');
    });
});
