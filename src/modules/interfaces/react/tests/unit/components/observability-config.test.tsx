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
            buttons[0].props.onClick();
            buttons[1].props.onClick();
            buttons[2].props.onClick();
            select.props.onChange({target: {value: 'staging'}});
            inputs.find(input => input.props.value === 3)!.props.onChange({target: {value: '7'}});
        });

        expect(onConfigChange).toHaveBeenCalledWith({observability: false});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHostOverride: true});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseSecretKey: 'secret'});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHost: 'http://localhost:3000', langfuseHostOverride: false});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHost: 'http://langfuse-web:3000', langfuseHostOverride: true});
        expect(onConfigChange).toHaveBeenCalledWith({langfuseHost: 'https://cloud.langfuse.com', langfuseHostOverride: true});
        expect(onConfigChange).toHaveBeenCalledWith({langfusePromptLabel: 'staging'});
        expect(onConfigChange).toHaveBeenCalledWith({minToolCalls: 7});
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
});
