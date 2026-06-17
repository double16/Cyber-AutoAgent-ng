import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {jest} from '@jest/globals';
import {useCommandHandler} from '../../../src/hooks/useCommandHandler.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const detailedHealth = {
    status: {
        lastCheck: new Date('2026-06-17T12:00:00Z'),
        overall: 'warning',
        dockerRunning: true,
        services: [
            {
                displayName: 'Langfuse',
                status: 'running',
                health: 'healthy',
                uptime: '1h',
                message: 'ok',
            },
            {
                displayName: 'Postgres',
                status: 'stopped',
                health: 'unhealthy',
            },
        ],
    },
    recommendations: ['Restart Postgres'],
};

const healthMonitor = {
    getDetailedHealth: jest.fn(async () => detailedHealth),
};

const containerManager = {
    getCurrentMode: jest.fn(async () => 'single-container'),
};

jest.unstable_mockModule('../../../src/services/HealthMonitor.js', () => ({
    HealthMonitor: {
        getInstance: jest.fn(() => healthMonitor),
    },
}));

jest.unstable_mockModule('../../../src/services/ContainerManager.js', () => ({
    ContainerManager: {
        getInstance: jest.fn(() => containerManager),
    },
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

const createProps = (overrides: Record<string, any> = {}) => {
    const history: Array<{ type: string; content: string; operation?: any }> = [];
    const assessmentState = {
        module: 'web',
        target: 'example.com',
        objective: 'test objective',
        stage: 'ready',
    };

    return {
        history,
        props: {
            commandParser: {
                parse: jest.fn((input: string) => {
                    if (input.startsWith('/')) {
                        const [command, ...args] = input.slice(1).split(/\s+/);
                        return {type: 'slash', command, args};
                    }
                    return {type: 'flow'};
                }),
                getAvailableModules: jest.fn(() => ['web', 'api']),
            },
            assessmentFlowManager: {
                processUserInput: jest.fn(() => ({success: true, message: 'accepted', nextPrompt: 'next'})),
                isReadyForAssessmentExecution: jest.fn(() => false),
                getState: jest.fn(() => assessmentState),
            },
            operationManager: {},
            appState: {
                userHandoffActive: false,
                executionService: null,
            },
            actions: {
                setUserHandoff: jest.fn(),
                setInitializationFlow: jest.fn(),
            },
            applicationConfig: {
                deploymentMode: 'full-stack',
            },
            addOperationHistoryEntry: jest.fn((type: string, content: string, operation?: any) => {
                history.push({type, content, operation});
            }),
            openConfig: jest.fn(),
            openMemorySearch: jest.fn(),
            openModuleSelector: jest.fn((callback: (moduleName: string) => void) => callback('api')),
            openSafetyWarning: jest.fn(),
            openDocumentation: jest.fn(),
            handleScreenClear: jest.fn(),
            refreshStatic: jest.fn(),
            modalManager: {},
            setAssessmentFlowState: jest.fn(),
            requestExit: jest.fn(),
            ...overrides,
        },
    };
};

describe('useCommandHandler', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        healthMonitor.getDetailedHealth.mockResolvedValue(detailedHealth);
        containerManager.getCurrentMode.mockResolvedValue('single-container');
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.restoreAllMocks();
        delete process.env.CYBER_SHOW_SETUP;
        delete process.env.CYBER_TEST_MODE;
    });

    it('ignores blank input and routes handoff responses to execution services', async () => {
        const sendUserInput = jest.fn(async () => undefined);
        const {props} = createProps({
            appState: {
                userHandoffActive: true,
                executionService: {sendUserInput},
            },
        });
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleUnifiedInput('   ');
            await hook.current.handleUnifiedInput('y');
        });

        expect(sendUserInput).toHaveBeenCalledWith('y');
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('info', 'User response sent: y');
        expect(props.actions.setUserHandoff).toHaveBeenCalledWith(false);
        expect(props.commandParser.parse).not.toHaveBeenCalled();

        hook.unmount();
    });

    it('reports handoff services that cannot accept input or fail', async () => {
        const {props} = createProps({
            appState: {
                userHandoffActive: true,
                executionService: {},
            },
        });
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleUnifiedInput('hello');
        });
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', 'Current execution service does not support user input');

        props.appState.executionService = {
            sendUserInput: jest.fn(async () => {
                throw new Error('closed');
            }),
        };
        await act(async () => {
            await hook.current.handleUnifiedInput('again');
        });
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', expect.stringContaining('Failed to send input'));

        hook.unmount();
    });

    it('handles common slash commands and aliases', async () => {
        const {props, history} = createProps();
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleSlashCommand('c', []);
            await hook.current.handleSlashCommand('memory', []);
            await hook.current.handleSlashCommand('clr', []);
            await hook.current.handleSlashCommand('help', []);
            await hook.current.handleSlashCommand('setup', []);
            await hook.current.handleSlashCommand('q', []);
            jest.runOnlyPendingTimers();
        });

        expect(props.openConfig).toHaveBeenCalled();
        expect(props.handleScreenClear).toHaveBeenCalled();
        expect(props.refreshStatic).toHaveBeenCalled();
        expect(props.actions.setInitializationFlow).toHaveBeenCalledWith(true, true);
        expect(props.requestExit).toHaveBeenCalled();
        expect(process.env.CYBER_SHOW_SETUP).toBe('true');
        expect(history.map(entry => entry.content).join('\n')).toContain('Cyber-AutoAgent Command Reference');
        expect(history.map(entry => entry.content).join('\n')).toContain('Memory operations require');

        hook.unmount();
    });

    it('handles plugin selection, docs, and unknown slash commands', async () => {
        const {props} = createProps();
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleSlashCommand('plugins', ['web']);
            await hook.current.handleSlashCommand('plugins', ['missing']);
            await hook.current.handleSlashCommand('plugins', []);
            await hook.current.handleSlashCommand('docs', ['2']);
            await hook.current.handleSlashCommand('docs', ['9']);
            await hook.current.handleSlashCommand('wat', []);
        });

        expect(props.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('module web');
        expect(props.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('module api');
        expect(props.openModuleSelector).toHaveBeenCalled();
        expect(props.openDocumentation).toHaveBeenCalledWith(2);
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', expect.stringContaining('Unknown plugin'));
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', 'Invalid document number. Please use a number between 1 and 7.');
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', 'Unknown command: /wat. Type /help for available commands.');

        hook.unmount();
    });

    it('builds health check reports and mismatch recommendations', async () => {
        const {props} = createProps();
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleSlashCommand('health', []);
        });

        const report = (props.addOperationHistoryEntry as jest.Mock).mock.calls
            .map(call => call[1])
            .join('\n');
        expect(report).toContain('SYSTEM HEALTH REPORT');
        expect(report).toContain('Langfuse: RUNNING');
        expect(report).toContain('Restart Postgres');
        expect(report).toContain('Detected deployment mode differs from configured mode');

        hook.unmount();
    });

    it('handles health check failures', async () => {
        healthMonitor.getDetailedHealth.mockRejectedValueOnce(new Error('no health'));
        const {props} = createProps();
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleSlashCommand('health', []);
        });

        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', expect.stringContaining('Health check failed'));
        hook.unmount();
    });

    it('routes parsed input and reports unknown or thrown command handling errors', async () => {
        const {props} = createProps({
            openConfig: jest.fn(() => {
                throw new Error('modal failed');
            }),
        });
        props.commandParser.parse
            .mockReturnValueOnce({type: 'slash', command: 'help', args: []})
            .mockReturnValueOnce({type: 'unknown'})
            .mockReturnValueOnce({type: 'slash', command: 'config', args: []})
            .mockImplementationOnce(() => {
                throw new Error('parse failed');
            });
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleUnifiedInput('/help');
            await hook.current.handleUnifiedInput('???');
            await hook.current.handleUnifiedInput('/config');
        });
        await expect(hook.current.handleUnifiedInput('boom')).rejects.toThrow('parse failed');

        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', 'Unknown command format. Type /help for available commands.');
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', expect.stringContaining('Input handling error'));
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', expect.stringContaining('Error processing command'));

        hook.unmount();
    });

    it('handles natural language commands and missing targets', async () => {
        const {props} = createProps();
        props.assessmentFlowManager.isReadyForAssessmentExecution.mockReturnValue(true);
        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleNaturalLanguageCommand({type: 'natural'} as any);
            await hook.current.handleNaturalLanguageCommand({
                type: 'natural',
                module: 'web',
                target: 'example.com',
                objective: 'find xss',
            } as any);
        });

        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', 'Invalid natural language command. Missing target.');
        expect(props.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('module web');
        expect(props.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('target example.com');
        expect(props.assessmentFlowManager.processUserInput).toHaveBeenCalledWith('objective find xss');
        expect(props.openSafetyWarning).toHaveBeenCalledWith({
            module: 'web',
            target: 'example.com',
            objective: 'test objective',
        });

        hook.unmount();
    });

    it('handles guided flow success, errors, and execute shortcuts', async () => {
        const {props} = createProps();
        props.assessmentFlowManager.isReadyForAssessmentExecution
            .mockReturnValueOnce(true)
            .mockReturnValueOnce(false)
            .mockReturnValueOnce(true)
            .mockReturnValueOnce(true);
        props.assessmentFlowManager.processUserInput
            .mockReturnValueOnce({error: 'bad input'})
            .mockReturnValueOnce({success: true, message: 'ok', nextPrompt: 'objective?', readyToExecute: true})
            .mockReturnValueOnce({success: true, message: 'empty ok'});

        const hook = renderHook(() => useCommandHandler(props as any));

        await act(async () => {
            await hook.current.handleGuidedFlowInput('execute');
            await hook.current.handleGuidedFlowInput('bad');
            await hook.current.handleGuidedFlowInput('objective');
            await hook.current.handleGuidedFlowInput('');
        });

        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('error', 'bad input');
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('info', 'ok');
        expect(props.addOperationHistoryEntry).toHaveBeenCalledWith('info', '→ objective?');
        expect(props.setAssessmentFlowState).toHaveBeenCalledWith(expect.objectContaining({
            step: 'ready',
            module: 'web',
            target: 'example.com',
        }));
        expect(props.openSafetyWarning).toHaveBeenCalled();

        hook.unmount();
    });
});
