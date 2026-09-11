import {
  detectTerminalBackground,
  getRecommendedThemeType,
  supportsRichColors,
} from '../../../src/themes/terminal-detector.js';

describe('terminal-detector', () => {
    const originalEnv = {...process.env};

    beforeEach(() => {
        process.env = {...originalEnv};
        delete process.env.COLORFGBG;
        delete process.env.TERM;
        delete process.env.COLORTERM;
        delete process.env.TERM_PROGRAM;
        delete process.env.TERM_PROGRAM_VERSION;
        delete process.env.CYBER_THEME;
        delete process.env.NO_COLOR;
    });

    afterEach(() => {
        process.env = {...originalEnv};
    });

    it('detects light and dark backgrounds from COLORFGBG', () => {
        process.env.COLORFGBG = '15;0';
        expect(detectTerminalBackground()).toBe('dark');

        process.env.COLORFGBG = '0;15';
        expect(detectTerminalBackground()).toBe('light');
    });

    it('defaults known terminal programs and explicit theme envs', () => {
        process.env.TERM_PROGRAM = 'iTerm.app';
        expect(detectTerminalBackground()).toBe('dark');

        delete process.env.TERM_PROGRAM;
        process.env.CYBER_THEME = 'light';
        expect(detectTerminalBackground()).toBe('light');

        process.env.CYBER_THEME = 'dark';
        expect(detectTerminalBackground()).toBe('dark');
    });

    it('detects rich color support from TERM, COLORTERM, and terminal program', () => {
        process.env.TERM = 'xterm-256color';
        expect(supportsRichColors()).toBe(true);

        process.env.TERM = 'xterm';
        process.env.COLORTERM = 'truecolor';
        expect(supportsRichColors()).toBe(true);

        delete process.env.COLORTERM;
        process.env.TERM_PROGRAM = 'vscode';
        expect(supportsRichColors()).toBe(true);

        process.env.TERM_PROGRAM = 'unknown';
        expect(supportsRichColors()).toBe(false);
    });

    it('recommends light only for detected light backgrounds', () => {
        process.env.COLORFGBG = '0;15';
        expect(getRecommendedThemeType()).toBe('light');

        process.env.COLORFGBG = '15;0';
        expect(getRecommendedThemeType()).toBe('dark');
    });
});
