import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

jest.unstable_mockModule('ink-spinner', () => ({
    default: ({type}: { type?: string }) => <span>spinner:{type}</span>,
}));

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

describe('setup screens with zero coverage', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        delete (global as any).__inkInputHandler;
        delete (global as any).__SETUP_RATIO__;
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('WelcomeScreen renders setup copy and handles continue and skip keys', async () => {
        const {WelcomeScreen} = await import('../../../src/components/setup/WelcomeScreen.js');
        const onContinue = jest.fn();
        const onSkip = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<WelcomeScreen onContinue={onContinue} onSkip={onSkip} terminalWidth={60}/>);
        });

        expect(textFromTree(view.toJSON())).toContain('Welcome to Cyber-AutoAgent');
        sendInput('', {return: true});
        sendInput(' ');
        sendInput('', {escape: true});

        expect(onContinue).toHaveBeenCalledTimes(2);
        expect(onSkip).toHaveBeenCalledTimes(1);
    });

    it('ProgressScreen renders progress, error, complete, and keyboard actions', async () => {
        const {ProgressScreen} = await import('../../../src/components/setup/ProgressScreen.js');
        const onComplete = jest.fn();
        const onRetry = jest.fn();
        const onBack = jest.fn();

        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <ProgressScreen
                    deploymentMode="single-container"
                    progress={{stepName: 'containers-start', message: '[OK] starting', meta: {phaseRatio: 0.5, running: 1, required: 2}} as any}
                    isComplete={false}
                    isLoading
                    error={null}
                    onComplete={onComplete}
                    onRetry={onRetry}
                    onBack={onBack}
                    terminalWidth={70}
                />
            );
        });

        expect(textFromTree(view.toJSON())).toContain('Setting up Single Container');
        expect(textFromTree(view.toJSON())).toContain('1/2 services started');
        act(() => {
            jest.advanceTimersByTime(1000);
        });
        expect(textFromTree(view.toJSON())).toContain('Elapsed: 0:01');

        sendInput('', {escape: true});
        expect(onBack).toHaveBeenCalledTimes(1);

        act(() => {
            view.update(
                <ProgressScreen
                    deploymentMode="local-cli"
                    progress={{stepName: 'environment', message: 'failed'} as any}
                    isComplete={false}
                    isLoading={false}
                    error="Python missing"
                    onComplete={onComplete}
                    onRetry={onRetry}
                    onBack={onBack}
                    terminalWidth={70}
                />
            );
        });
        expect(textFromTree(view.toJSON())).toContain('Setup Failed');
        sendInput('r');
        expect(onRetry).toHaveBeenCalledTimes(1);

        act(() => {
            view.update(
                <ProgressScreen
                    deploymentMode="full-stack"
                    progress={{stepName: 'validation', message: 'done'} as any}
                    isComplete
                    isLoading={false}
                    error={null}
                    onComplete={onComplete}
                    onRetry={onRetry}
                    onBack={onBack}
                    terminalWidth={70}
                />
            );
        });
        expect(textFromTree(view.toJSON())).toContain('Setup Complete');
        sendInput('', {return: true});
        expect(onComplete).toHaveBeenCalledTimes(1);
    });
});
