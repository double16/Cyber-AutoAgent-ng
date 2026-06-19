import {themeManager} from '../../../src/themes/theme-manager.js';
import {CyberDarkTheme} from '../../../src/themes/cyber-dark.js';
import {CyberLightTheme} from '../../../src/themes/cyber-light.js';

describe('themeManager', () => {
    afterEach(() => {
        themeManager.useDarkTheme();
        themeManager.updateTerminalWidth(80);
    });

    it('returns and updates theme configuration', () => {
        themeManager.useLightTheme();

        expect(themeManager.getCurrentTheme()).toBe(CyberLightTheme);
        expect(themeManager.getConfig().theme).toBe(CyberLightTheme);
        expect(themeManager.isLightTheme()).toBe(true);
        expect(themeManager.isDarkTheme()).toBe(false);
    });

    it('toggles between dark and light themes', () => {
        themeManager.useDarkTheme();
        expect(themeManager.getCurrentTheme()).toBe(CyberDarkTheme);

        themeManager.toggleTheme();
        expect(themeManager.getCurrentTheme()).toBe(CyberLightTheme);

        themeManager.toggleTheme();
        expect(themeManager.getCurrentTheme()).toBe(CyberDarkTheme);
    });

    it('uses terminal width to choose logo size', () => {
        themeManager.updateTerminalWidth(79);
        expect(themeManager.getLogoSize()).toBe('short');

        themeManager.updateTerminalWidth(80);
        expect(themeManager.getLogoSize()).toBe('long');
    });

    it('maps semantic colors to the active theme', () => {
        themeManager.useDarkTheme();
        const theme = themeManager.getCurrentTheme();

        expect(themeManager.getSemanticColor('tool')).toBe(theme.success);
        expect(themeManager.getSemanticColor('reasoning')).toBe(theme.info);
        expect(themeManager.getSemanticColor('output')).toBe(theme.foreground);
        expect(themeManager.getSemanticColor('error')).toBe(theme.danger);
        expect(themeManager.getSemanticColor('warning')).toBe(theme.warning);
        expect(themeManager.getSemanticColor('step')).toBe(theme.primary);
    });

    it('only uses gradients when enabled and available on the current theme', () => {
        const config = themeManager.getConfig() as any;
        const previous = config.enableGradients;

        try {
            themeManager.useDarkTheme();
            config.enableGradients = false;
            expect(themeManager.shouldUseGradient()).toBe(false);

            config.enableGradients = true;
            expect(themeManager.shouldUseGradient()).toBe(Boolean(themeManager.getCurrentTheme().gradientColors));
        } finally {
            config.enableGradients = previous;
        }
    });
});
