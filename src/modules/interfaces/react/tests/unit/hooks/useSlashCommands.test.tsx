import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {jest} from '@jest/globals';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const switchModule = jest.fn();
const updateConfig = jest.fn();

jest.unstable_mockModule('../../../src/contexts/ModuleContext.js', () => ({
    useModule: () => ({
        switchModule,
        currentModule: 'web',
        availableModules: {
            web: 'Web assessment',
            api: 'API assessment',
        },
    }),
}));

jest.unstable_mockModule('../../../src/contexts/ConfigContext.js', () => ({
    useConfig: () => ({
        config: {awsRegion: 'us-east-1'},
        updateConfig,
    }),
}));

function renderHook<T>(hook: () => T) {
    let current: T;
    const Harness = () => {
        current = hook();
        return null;
    };

    let renderer: TestRenderer.ReactTestRenderer;
    act(() => {
        renderer = TestRenderer.create(<Harness/>);
    });

    return {
        get current() {
            return current!;
        },
        unmount() {
            act(() => {
                renderer.unmount();
            });
        },
    };
}

describe('useSlashCommands', () => {
    afterEach(() => {
        jest.clearAllMocks();
    });

    it('returns supported command metadata and suggestions', async () => {
        const {useSlashCommands} = await import('../../../src/hooks/useSlashCommands.js');
        const hook = renderHook(() => useSlashCommands());

        const commands = hook.current.getSlashCommands();
        expect(commands.map(command => command.command)).toEqual([
            '/help',
            '/health',
            '/docs',
            '/plugins',
            '/config',
            '/setup',
            '/region',
            '/clear',
            '/exit',
        ]);
        expect(commands.find(command => command.command === '/docs')?.args).toEqual(['document_number']);
        expect(hook.current.getCommandSuggestions('/c').map(command => command.command)).toEqual(['/config', '/clear']);
        expect(hook.current.getCommandSuggestions('/DOC')).toEqual([
            expect.objectContaining({command: '/docs'}),
        ]);

        hook.unmount();
    });

    it('wraps command action errors with command context', async () => {
        const {useSlashCommands} = await import('../../../src/hooks/useSlashCommands.js');
        const hook = renderHook(() => useSlashCommands());

        await expect(hook.current.executeSlashCommand('/help')).rejects.toThrow(
            'Error executing /help: Help command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/docs 9')).rejects.toThrow(
            'Error executing /docs: Invalid document number. Please use a number between 1 and 7.'
        );
        await expect(hook.current.executeSlashCommand('/docs 2')).rejects.toThrow(
            'Error executing /docs: Documentation command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/plugins web')).rejects.toThrow(
            'Error executing /plugins: Plugins command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/config')).rejects.toThrow(
            'Error executing /config: Config command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/setup')).rejects.toThrow(
            'Error executing /setup: Setup command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/region us-west-2')).rejects.toThrow(
            'Error executing /region: Region command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/clear')).rejects.toThrow(
            'Error executing /clear: Clear command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/exit')).rejects.toThrow(
            'Error executing /exit: Exit command should be handled by useCommandHandler'
        );
        await expect(hook.current.executeSlashCommand('/unknown')).rejects.toThrow(
            'Unknown command: /unknown. Type /help for available commands.'
        );

        hook.unmount();
    });
});
