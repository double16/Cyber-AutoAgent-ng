import {NeonGridDark} from '../../../src/themes/neon-grid-dark.js';
import {NeonGridLight} from '../../../src/themes/neon-grid-light.js';
import {CyberDarkTheme} from '../../../src/themes/cyber-dark.js';
import {CyberLightTheme} from '../../../src/themes/cyber-light.js';
import {describe, expect, it} from '@jest/globals';

describe('theme constants', () => {
    it('exports complete neon grid dark theme', () => {
        expect(NeonGridDark).toEqual(expect.objectContaining({
            type: 'dark',
            name: 'Terminal Pro',
            background: '#000000',
            foreground: '#FFFFFF',
            primary: '#00FF41',
            success: '#00FF41',
        }));
        expect(NeonGridDark.gradientColors).toHaveLength(5);
    });

    it('exports complete neon grid light theme', () => {
        expect(NeonGridLight).toEqual(expect.objectContaining({
            type: 'light',
            name: 'Terminal Pro Light',
            background: '#FFFFFF',
            foreground: '#000000',
            primary: '#00A400',
            success: '#00A400',
        }));
        expect(NeonGridLight.gradientColors).toHaveLength(5);
    });

    it('keeps built-in cyber themes structurally complete', () => {
        for (const theme of [CyberDarkTheme, CyberLightTheme]) {
            expect(theme.name).toBeTruthy();
            expect(theme.background).toMatch(/^#/);
            expect(theme.foreground).toMatch(/^#/);
            expect(theme.primary).toMatch(/^#/);
            expect(theme.success).toMatch(/^#/);
            expect(theme.danger).toMatch(/^#/);
            expect(theme.warning).toMatch(/^#/);
            expect(theme.info).toMatch(/^#/);
            expect(theme.gradientColors?.length).toBeGreaterThan(0);
        }
    });
});
