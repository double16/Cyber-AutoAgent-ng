import {AssessmentFlow} from '../../../src/services/AssessmentFlow.js';

describe('AssessmentFlow', () => {
    it('starts at target stage with the default web module', () => {
        const flow = new AssessmentFlow();

        expect(flow.getState()).toEqual({
            stage: 'target',
            module: 'web',
            target: null,
            objective: null,
        });
        expect(flow.getCurrentPrompt()).toBe('[web] > ');
        expect(flow.isReadyForAssessmentExecution()).toBe(false);
        expect(flow.getValidatedAssessmentParameters()).toBeNull();
    });

    it('walks target and default objective stages into validated parameters', () => {
        const flow = new AssessmentFlow();

        const targetResult = flow.processUserInput('target https://example.com');
        expect(targetResult).toEqual(expect.objectContaining({
            success: true,
            message: 'Assessment target defined: https://example.com',
        }));
        expect(flow.getCurrentWorkflowStage()).toBe('objective');

        const objectiveResult = flow.processUserInput('');
        expect(objectiveResult).toEqual(expect.objectContaining({
            success: true,
            message: 'Using default web assessment objective',
        }));
        expect(flow.isReadyForAssessmentExecution()).toBe(true);
        expect(flow.getValidatedAssessmentParameters()).toEqual({
            module: 'web',
            target: 'https://example.com',
            objective: 'web security assessment and reconnaissance',
        });
    });

    it('triggers immediate execution when objective input is execute', () => {
        const flow = new AssessmentFlow();

        flow.processUserInput('target example.com');
        const result = flow.processUserInput('execute focus on auth bypass');

        expect(result).toEqual(expect.objectContaining({
            success: true,
            message: 'Custom objective configured: focus on auth bypass',
            readyToExecute: true,
        }));
        expect(flow.getValidatedAssessmentParameters()).toEqual({
            module: 'web',
            target: 'example.com',
            objective: 'focus on auth bypass',
        });
    });

    it('allows objective updates from ready state without immediate execution', () => {
        const flow = new AssessmentFlow();

        flow.processUserInput('target example.com');
        flow.processUserInput('custom objective');

        const result = flow.processUserInput('objective updated objective');

        expect(result).toEqual(expect.objectContaining({
            success: true,
            message: 'Objective updated: updated objective',
        }));
        expect(result.readyToExecute).toBeUndefined();
        expect(flow.getValidatedAssessmentParameters()).toEqual({
            module: 'web',
            target: 'example.com',
            objective: 'updated objective',
        });
    });

    it('allows execute with a new objective from ready state', () => {
        const flow = new AssessmentFlow();

        flow.processUserInput('target example.com');
        flow.processUserInput('initial objective');
        const result = flow.processUserInput('execute final objective');

        expect(result).toEqual(expect.objectContaining({
            success: true,
            readyToExecute: true,
            message: 'Custom objective configured: final objective',
        }));
        expect(flow.getValidatedAssessmentParameters()?.objective).toBe('final objective');
    });

    it('validates target command shape and non-empty target', () => {
        const flow = new AssessmentFlow();

        expect(flow.processUserInput('example.com')).toEqual(expect.objectContaining({
            success: false,
            error: 'Usage: target <target_specification>',
        }));

        expect(flow.processUserInput('target    ')).toEqual(expect.objectContaining({
            success: false,
            error: 'Usage: target <target_specification>',
        }));
    });

    it('supports dynamic modules, pending-discovery modules, and reset defaults', () => {
        const flow = new AssessmentFlow();

        flow.setSupportedModules(['web', 'ctf']);
        expect(flow.processUserInput('module ctf')).toEqual(expect.objectContaining({
            success: true,
            message: "Security module 'ctf' loaded successfully",
        }));

        expect(flow.processUserInput('module mobile')).toEqual(expect.objectContaining({
            success: true,
            message: "Security module 'mobile' selected (pending discovery)",
        }));

        flow.setDefaultModule('ctf');
        flow.resetCompleteWorkflow();
        expect(flow.getState()).toEqual(expect.objectContaining({
            stage: 'target',
            module: 'ctf',
            target: null,
            objective: null,
        }));
    });

    it('resets target configuration while preserving module', () => {
        const flow = new AssessmentFlow();

        flow.processUserInput('module ctf');
        flow.processUserInput('target challenge.local');
        flow.processUserInput('find flags');

        flow.resetToTargetConfiguration();

        expect(flow.getState()).toEqual(expect.objectContaining({
            stage: 'target',
            module: 'ctf',
            target: null,
            objective: null,
        }));
    });

    it('returns stage-specific help and ready-state guidance', () => {
        const flow = new AssessmentFlow();

        expect(flow.getHelp()).toContain('Module loaded. Now set target');

        flow.processUserInput('target example.com');
        expect(flow.getCurrentPrompt()).toBe('[web → example.com] > ');
        expect(flow.getHelp()).toContain('Target set. Enter objective');

        flow.processUserInput('objective');
        expect(flow.processUserInput('anything else')).toEqual(expect.objectContaining({
            success: false,
            message: 'Assessment configuration complete. Press Enter to start or type "reset" to reconfigure.',
        }));
        expect(flow.getHelp()).toContain('Ready to assess');
    });

    it('handles reset command globally', () => {
        const flow = new AssessmentFlow();

        flow.processUserInput('target example.com');
        const result = flow.processUserInput('reset');

        expect(result).toEqual(expect.objectContaining({
            success: true,
            message: 'Assessment workflow reset. Please specify a target.',
        }));
        expect(flow.getState()).toEqual({
            stage: 'target',
            module: 'web',
            target: null,
            objective: null,
        });
    });
});
