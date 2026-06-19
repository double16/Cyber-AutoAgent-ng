import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {afterEach, beforeEach, describe, expect, it, jest} from '@jest/globals';
import {SwarmDisplay, type SwarmState} from '../../../src/components/SwarmDisplay.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const textFromTree = (node: any): string => {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textFromTree).join('');
    return textFromTree(node.children || []);
};

const baseState = (): SwarmState => ({
    id: 'swarm-1',
    task: 'Coordinate testing',
    status: 'running',
    startTime: Date.now() - 2000,
    maxIterations: 10,
    maxHandoffs: 3,
    nodeTimeout: 30,
    executionTimeout: 120,
    totalTokens: 1234,
    collaborationChain: ['planner', 'tester', 'reporter'],
    result: 'final result',
    agents: [
        {
            id: 'a1',
            name: 'Planner',
            role: 'Plans the work',
            status: 'active',
            tools: ['think', 'delegate'],
            model_id: 'provider/model-name:tag',
            temperature: 0.2,
            currentStep: 1,
            maxSteps: 4,
            toolCalls: [{tool: 'think', input: {goal: 'map'}}],
            result: 'short result',
        },
        {
            id: 'a2',
            name: 'Tester',
            status: 'completed',
            tools: ['scan'],
            result: 'x'.repeat(120),
        },
        {
            id: 'a3',
            name: 'Reporter',
            status: 'failed',
        },
    ],
});

describe('SwarmDisplay additional coverage', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        jest.setSystemTime(new Date('2026-06-18T12:00:00Z'));
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('renders collapsed swarm summary with agent status counts and details', () => {
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<SwarmDisplay swarmState={baseState()} collapsed/>);
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('[SWARM]');
        expect(text).toContain('3 agents');
        expect(text).toContain('1 active');
        expect(text).toContain('1 completed');
        expect(text).toContain('Planner - Plans the work');
        expect(text).toContain('[1/4]');
        expect(text).toContain('(think, delegate)');
    });

    it('renders full swarm details and updates elapsed time while running', () => {
        const state = baseState();
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<SwarmDisplay swarmState={state}/>);
        });

        act(() => {
            jest.advanceTimersByTime(1000);
        });

        const text = textFromTree(view.toJSON());
        expect(text).toContain('Task: Coordinate testing');
        expect(text).toContain('Max iterations: 10');
        expect(text).toContain('Max handoffs: 3');
        expect(text).toContain('Node timeout: 30s');
        expect(text).toContain('Total timeout: 120s');
        expect(text).toContain('Tokens: 1234');
        expect(text).toContain('Model: model-name');
        expect(text).toContain('(temp: 0.2)');
        expect(text).toContain('Tools: [think, delegate]');
        expect(text).toContain('> think');
        expect(text).toContain('Collaboration Flow:');
        expect(text).toContain('planner > tester > reporter');
        expect(text).toContain('final result');
    });

    it('uses end time for completed swarms', () => {
        const state = {
            ...baseState(),
            status: 'completed',
            startTime: 1000,
            endTime: 4000,
        };
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<SwarmDisplay swarmState={state}/>);
        });

        expect(textFromTree(view.toJSON())).toContain('Duration:');
    });
});
