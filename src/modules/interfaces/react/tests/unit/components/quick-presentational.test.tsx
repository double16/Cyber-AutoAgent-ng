import React from 'react';
import {TextDecoder, TextEncoder} from 'util';
import {describe, expect, it} from '@jest/globals';
import {Box, Text} from 'ink';

if (typeof global.TextEncoder === 'undefined') {
    global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
    global.TextDecoder = TextDecoder as typeof global.TextDecoder;
}

const loadComponents = async () => {
    const [
        {render},
        {OperationFooter},
        {DeploymentWarning},
        {MaxSizedBox},
    ] = await Promise.all([
        import('ink-testing-library'),
        import('../../../src/components/OperationFooter.js'),
        import('../../../src/components/DeploymentWarning.js'),
        import('../../../src/components/MaxSizedBox.js'),
    ]);

    return {render, OperationFooter, DeploymentWarning, MaxSizedBox};
};

describe('quick presentational component coverage', () => {
    it('renders operation footer metrics', async () => {
        const {render, OperationFooter} = await loadComponents();
        const {lastFrame} = render(
            <OperationFooter
                tokens={1234567}
                duration="1m 2s"
                memoryOps={3}
                evidence={4}
            />
        );

        const frame = lastFrame();
        expect(frame).toContain('1,234,567');
        expect(frame).toContain('1m 2s');
        expect(frame).toContain('3');
        expect(frame).toContain('4');
        expect(frame).toContain('[CTRL+C] Kill operation');
    });

    it('does not render deployment warning when zero or one deployment is active', async () => {
        const {render, DeploymentWarning} = await loadComponents();
        expect(render(
            <DeploymentWarning activeDeployments={[]} configuredMode="local-cli"/>
        ).lastFrame()).toBe('');

        expect(render(
            <DeploymentWarning
                activeDeployments={[{mode: 'local-cli', isHealthy: true} as any]}
                configuredMode="local-cli"
            />
        ).lastFrame()).toBe('');
    });

    it('renders unused deployment warnings and cleanup suggestions', async () => {
        const {render, DeploymentWarning} = await loadComponents();
        const {lastFrame} = render(
            <DeploymentWarning
                configuredMode="local-cli"
                activeDeployments={[
                    {mode: 'local-cli', isHealthy: true} as any,
                    {mode: 'single-container', isHealthy: true} as any,
                    {mode: 'full-stack', isHealthy: true} as any,
                ]}
            />
        );

        const frame = lastFrame();
        expect(frame).toContain('Multiple Deployments Detected');
        expect(frame).toContain('Active: local-cli');
        expect(frame).toContain('single-container');
        expect(frame).toContain('full-stack');
        expect(frame).toContain('docker stop cyber-autoagent');
        expect(frame).toContain('docker compose down');
    });

    it('truncates MaxSizedBox content from the top by default', async () => {
        const {render, MaxSizedBox} = await loadComponents();
        const {lastFrame} = render(
            <MaxSizedBox maxWidth={80} maxHeight={3}>
                <Box><Text>line 1</Text></Box>
                <Box><Text>line 2</Text></Box>
                <Box><Text>line 3</Text></Box>
                <Box><Text>line 4</Text></Box>
            </MaxSizedBox>
        );

        const frame = lastFrame();
        expect(frame).toContain('first 2 lines');
        expect(frame).not.toContain('line 1');
        expect(frame).not.toContain('line 2');
        expect(frame).toContain('line 3');
        expect(frame).toContain('line 4');
    });

    it('truncates MaxSizedBox content from the bottom when requested', async () => {
        const {render, MaxSizedBox} = await loadComponents();
        const {lastFrame} = render(
            <MaxSizedBox maxWidth={80} maxHeight={3} overflowDirection="bottom" additionalHiddenLinesCount={1}>
                <Box><Text>line 1</Text></Box>
                <Box><Text>line 2</Text></Box>
                <Box><Text>line 3</Text></Box>
            </MaxSizedBox>
        );

        const frame = lastFrame();
        expect(frame).toContain('line 1');
        expect(frame).toContain('line 2');
        expect(frame).not.toContain('line 3');
        expect(frame).toContain('last 2 lines');
    });
});
