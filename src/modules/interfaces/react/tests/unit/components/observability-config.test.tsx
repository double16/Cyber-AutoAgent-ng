import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
import {describe, expect, it, jest} from '@jest/globals';
import {ObservabilityConfig} from '../../../src/components/ObservabilityConfig.js';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const baseConfig = {
    observability: true,
    langfuseHost: '',
    langfuseHostOverride: false,
    langfusePublicKey: '',
    langfuseSecretKey: '',
    enableLangfusePrompts: true,
    langfusePromptLabel: 'production',
    autoEvaluation: true,
    evaluationModel: 'judge-model',
    minToolCalls: 3,
    minEvidence: 1,
    evalMaxWaitSecs: 30,
    evalPollIntervalSecs: 5,
    evalSummaryMaxChars: 8000,
} as any;

describe('ObservabilityConfig', () => {
    it('renders enabled observability fields and emits config changes', () => {
        const onConfigChange = jest.fn();
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(<ObservabilityConfig config={baseConfig} onConfigChange={onConfigChange}/>);
        });

        const inputs = view.root.findAllByType('input');
        const buttons = view.root.findAllByType('button');
        const select = view.root.findByType('select');

        act(() => {
            inputs.find(input => input.props.id === 'observability')!.props.onChange({target: {checked: false}});
            inputs.find(input => input.props.id === 'langfuseHostOverride')!.props.onChange({target: {checked: true}});
            inputs.find(input => input.props.type === 'password')!.props.onChange({target: {value: 'secret'}});
            inputs[1].props.onChange({target: {value: 'http://trace.local'}});
            inputs[3].props.onChange({target: {value: 'public'}});
            buttons[0].props.onClick();
            buttons[1].props.onClick();
            buttons[2].props.onClick();
            select.props.onChange({target: {value: 'staging'}});
            inputs.find(input => input.props.value === 3)!.props.onChange({target: {value: '7'}});
            inputs.find(input => input.props.value === 1)!.props.onChange({target: {value: '2'}});
            inputs.find(input => input.props.value === 30)!.props.onChange({target: {value: '45'}});
            inputs.find(input => input.props.value === 5)!.props.onChange({target: {value: '9'}});
            inputs.find(input => input.props.value === 8000)!.props.onChange({target: {value: '12000'}});
            inputs.find(input => input.props.id === 'enableLangfusePrompts')!.props.onChange({target: {checked: false}});
            inputs.find(input => input.props.id === 'autoEvaluation')!.props.onChange({target: {checked: false}});
        });

        expect(onConfigChange).toHaveBeenCalledWith({observability: false});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHostOverride: true});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseSecretKey: 'secret'});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHost: 'http://trace.local'});
        expect(onConfigChange).toHaveBeenCalledWith({langfusePublicKey: 'public'});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHost: 'http://localhost:3000', langfuseHostOverride: false});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHost: 'http://langfuse-web:3000', langfuseHostOverride: true});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHost: 'https://cloud.langfuse.com', langfuseHostOverride: true});
        expect(onConfigChange).toHaveBeenCalledWith({langfusePromptLabel: 'staging'});
        expect(onConfigChange).toHaveBeenCalledWith({minToolCalls: 7});
        expect(onConfigChange).toHaveBeenCalledWith({minEvidence: 2});
        expect(onConfigChange).toHaveBeenCalledWith({evalMaxWaitSecs: 45});
        expect(onConfigChange).toHaveBeenCalledWith({evalPollIntervalSecs: 9});
        expect(onConfigChange).toHaveBeenCalledWith({evalSummaryMaxChars: 12000});
        expect(onConfigChange).toHaveBeenCalledWith({enableLangfusePrompts: false});
        expect(onConfigChange).toHaveBeenCalledWith({autoEvaluation: false});
    });

    it('renders disabled status without optional observability panels', () => {
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <ObservabilityConfig
                    config={{...baseConfig, observability: false, autoEvaluation: false}}
                    onConfigChange={jest.fn()}
                />
            );
        });
        const text = JSON.stringify(view.toJSON());

        expect(text).toContain('Observability disabled');
        expect(text).not.toContain('Langfuse Host');
        expect(text).not.toContain('Evaluation Model');
    });

    it('uses fallback values and auto-detected status copy', () => {
        let view!: TestRenderer.ReactTestRenderer;
        act(() => {
            view = TestRenderer.create(
                <ObservabilityConfig
                    config={{
                        ...baseConfig,
                        langfuseHost: 'http://configured',
                        langfuseHostOverride: false,
                        langfusePublicKey: 'pk',
                        langfuseSecretKey: 'sk',
                        enableLangfusePrompts: false,
                        langfusePromptLabel: undefined,
                        evaluationModel: '',
                        minToolCalls: undefined,
                        minEvidence: undefined,
                        evalMaxWaitSecs: undefined,
                        evalPollIntervalSecs: undefined,
                        evalSummaryMaxChars: undefined,
                    }}
                    onConfigChange={jest.fn()}
                />
            );
        });

        const inputs = view.root.findAllByType('input');
        expect(inputs.find(input => input.props.value === 3)).toBeDefined();
        expect(inputs.find(input => input.props.value === 1)).toBeDefined();
        expect(inputs.find(input => input.props.value === 30)).toBeDefined();
        expect(inputs.find(input => input.props.value === 5)).toBeDefined();
        expect(inputs.find(input => input.props.value === 8000)).toBeDefined();
        expect(JSON.stringify(view.toJSON())).toContain('auto-detected host');
    });
});
