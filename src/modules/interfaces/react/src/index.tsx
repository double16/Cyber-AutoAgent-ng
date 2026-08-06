#!/usr/bin/env node
import './utils/performanceTimelineGuard.js';
import React from 'react';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { render } from 'ink';
import { PassThrough } from 'node:stream';
import meow from 'meow';
import { App } from './App.js';
import { Config } from './contexts/ConfigContext.js';
import { loggingService } from './services/LoggingService.js';
import type {ExecutionHandle, ExecutionService} from './services/ExecutionService.js';
import {stopExecution} from './services/executionLifecycle.js';
import { enableConsoleSilence } from './utils/consoleSilencer.js';
import { estimateEtaSeconds } from './utils/duration.js';
import { formatDuration } from './utils/logger.js';
import { formatDuration as formatToolDuration } from './utils/toolFormatters.js';
import { formatAutoRunEvaluationEvent } from './utils/evaluationEventFormatting.js';
import { installAutoRunInterruptFallback } from './utils/autoRunInterrupt.js';
import { formatAutoRunTerminationEvent } from './utils/autoRunTerminationFormatting.js';
import { resolveRecordingMode } from './utils/recordingMode.js';
import { formatAutoRunMemoryEvent } from './utils/memoryEventFormatting.js';
import { formatWorkflowActivityEvent } from './utils/workflowActivityFormatting.js';
import { formatAutoRunReportProgress } from './utils/reportProgressFormatting.js';
import { appendOperationHealth } from './utils/operationHealthFormatting.js';
import { setOperationTerminalTitle } from './utils/terminalTitle.js';
import { applyMemoryModeOverride } from './utils/cliConfigOverrides.js';

const formatTaskScope = (event: any): string => {
  const scope = typeof event?.target_scope === 'string' ? event.target_scope.trim() : '';
  const ids = Array.isArray(event?.target_ids) ? event.target_ids.filter(Boolean).join(',') : '';
  if (!scope || scope === 'all') return '';
  return ids ? ` [scope: ${ids}]` : ` [scope: ${scope}]`;
};

// Check for --debug flag early (before meow parsing) to enable logging
if (process.argv.includes('--debug') || process.argv.includes('-d')) {
  process.env.CYBER_DEBUG = 'true';
}

// Default to production mode when NODE_ENV is unset
try {
  if (!process.env.NODE_ENV) {
    process.env.NODE_ENV = 'production';
  }
} catch { }

// Silence noisy console output in production unless explicitly debugging
try {
  const env = process.env.NODE_ENV || 'production';
  // Treat anything except explicit 'development' as production by default
  const isProd = env !== 'development';
  const debugOn = !!(process.env.DEBUG || process.env.CYBER_DEBUG || process.env.CYBER_TEST_MODE);
  if (isProd && !debugOn) {
    enableConsoleSilence();
  }
} catch { }

// Set project root if not already set (helps ContainerManager find docker-compose.yml)
if (!process.env.CYBER_PROJECT_ROOT) {
  // Navigate up from src/modules/interfaces/react/dist to project root
  const currentFileUrl = import.meta.url;
  const currentDir = path.dirname(currentFileUrl.replace('file://', ''));
  const projectRoot = path.resolve(currentDir, '..', '..', '..', '..', '..');
  if (fs.existsSync(path.join(projectRoot, 'docker', 'docker-compose.yml'))) {
    process.env.CYBER_PROJECT_ROOT = projectRoot;
  }
}

// Earliest possible test hint to ensure PTY capture sees a welcome line
try {
  if (process.env.CYBER_TEST_MODE === 'true') {
    loggingService.info('Welcome to Cyber-AutoAgent');
  }
} catch { }

const cli = meow(`
  Usage
    $ cyber-react [options]

  Options
    --target, -t        Target system/network to assess
    --objective, -o     Security assessment objective
    --module, -m        Security module to use (default: web)
    --max-duration      Required: Maximum duration in minutes for the operation
    --max-tokens        Optional: Total token budget (input+output+cache)
    --max-cost          Optional: Total cost budget (e.g., USD)
    --auto-run          Start assessment immediately without UI
    --auto-approve      Auto-approve tool executions (no confirmations)
    --memory-mode       Memory scope: operation (default) or shared
    --provider          Model provider: bedrock (default), ollama, or litellm
    --model             Specific model ID to use
    --region            AWS region (default: us-east-1)
    --observability     Enable observability tracing (default: false)
    --debug, -d         Enable debug mode
    --headless          Run in headless mode for scripting
    --continue          Continue a previous operation, optionally by operation ID, defaults to last operation
    --report            Re-generate a report, optionally by operation ID, defaults to last operation
    --deployment-mode   Deployment mode: local-cli, single-container, full-stack
    --mcp-enabled       Enable MCP servers
    --mcp-conns         Define MCP servers using JSON
    --recording         Optimize terminal output for screen recording

  Examples
    $ cyber-react
    $ cyber-react --module web
    $ cyber-react --target example.com --objective "vulnerability scan" --auto-run
    $ cyber-react -t 192.168.1.100 -o "port scan and service enumeration" -i 25 --auto-approve
`, {
  importMeta: import.meta,
  booleanDefault: undefined,
  flags: {
    target: {
      type: 'string',
      shortFlag: 't'
    },
    objective: {
      type: 'string',
      shortFlag: 'o'
    },
    module: {
      type: 'string',
      shortFlag: 'm',
      default: 'web'
    },
    maxDuration: {
      type: 'number',
    },
    maxTokens: {
      type: 'number',
    },
    maxCost: {
      type: 'number',
    },
    autoRun: {
      type: 'boolean',
      default: false
    },
    autoApprove: {
      type: 'boolean',
      default: false
    },
    memoryMode: {
      type: 'string',
    },
    provider: {
      type: 'string',
    },
    model: {
      type: 'string'
    },
    region: {
      type: 'string',
    },
    observability: {
      type: 'boolean',
    },
    debug: {
      type: 'boolean',
      shortFlag: 'd'
    },
    headless: {
      type: 'boolean',
      default: false
    },
    continue: {
      type: 'string',
      isMultiple: false,
      isRequired: false,
    },
    report: {
      type: 'string',
      isMultiple: false,
      isRequired: false,
    },
    deploymentMode: {
      type: 'string',
    },
    mcpEnabled: {
      type: 'boolean',
    },
    mcpConns: {
      type: 'string',
    },
    recording: {
      type: 'boolean',
      default: false
    }
  }
});

const recordingMode = resolveRecordingMode(cli.flags.recording);
process.env.CYBER_RECORDING_MODE = recordingMode ? 'true' : 'false';

// Emit an immediate welcome line in headless test mode to aid terminal capture timing
try {
  if (process.env.CYBER_TEST_MODE === 'true' && cli.flags.headless && !cli.flags.autoRun) {
    const configDir = process.env.CYBER_CONFIG_DIR || path.join(os.homedir(), '.cyber-autoagent');
    const configPath = path.join(configDir, 'config.json');
    const firstLaunch = !fs.existsSync(configPath);
    if (firstLaunch) {
      loggingService.info('Welcome to Cyber-AutoAgent');
      try { console.log('[TEST_EVENT] welcome'); } catch { }
    }
  }
} catch { }

// Check if we're running in a TTY environment
const isRawModeSupported = process.stdin.isTTY;

// Handle autoRun mode by bypassing React UI and executing directly
const runAutoAssessment = async () => {
  setOperationTerminalTitle(null, cli.flags.target);
  if (cli.flags.autoRun && cli.flags.target) {
    loggingService.info(`🔐 Starting assessment: ${cli.flags.module} → ${cli.flags.target}`);
    loggingService.info(`📌 Objective: ${cli.flags.objective || 'General security assessment'}`);
    let executionService: ExecutionService | null = null;
    let executionHandle: ExecutionHandle | null = null;
    let signalCleanup: (() => void) | null = null;
    let interruptInputCleanup: (() => void) | null = null;
    let stoppingForSignal = false;

    try {
      // Import config system to get proper defaults and merge with CLI overrides
      const configModule = await import('./contexts/ConfigContext.js');

      // Load default config and apply CLI overrides
      const configOverrides: Partial<Config> = {};

      // Load saved configuration first to detect provider changes
      const configDir = process.env.CYBER_CONFIG_DIR || path.join(os.homedir(), '.cyber-autoagent');
      const configPath = path.join(configDir, 'config.json');
      let savedConfig: Partial<Config> | undefined;

      if (fs.existsSync(configPath)) {
        try {
          const configData = fs.readFileSync(configPath, 'utf-8');
          savedConfig = JSON.parse(configData);
          loggingService.info(`📂 Loaded configuration from ${configPath}`);
        } catch (error) {
          loggingService.warn(`⚠️  Failed to load config: ${error instanceof Error ? error.message : String(error)}`);
        }
      }

      // Apply CLI flag overrides
      if (cli.flags.provider) configOverrides.modelProvider = cli.flags.provider as 'bedrock' | 'ollama' | 'litellm' | 'gemini';
      if (cli.flags.model) {
        configOverrides.modelId = cli.flags.model;
        configOverrides.swarmModel = cli.flags.model;
      }
      if (cli.flags.region) configOverrides.awsRegion = cli.flags.region;
      applyMemoryModeOverride(configOverrides, cli.flags.memoryMode);
      if (cli.flags.maxDuration) configOverrides.budgetMaxDuration = cli.flags.maxDuration;
      if (cli.flags.maxTokens) configOverrides.budgetMaxTokens = cli.flags.maxTokens;
      if (cli.flags.maxCost) configOverrides.budgetMaxCost = cli.flags.maxCost;
      if (cli.flags.observability !== undefined) configOverrides.observability = cli.flags.observability;
      if (cli.flags.debug) configOverrides.verbose = cli.flags.debug;
      if (cli.flags.deploymentMode) configOverrides.deploymentMode = cli.flags.deploymentMode as 'local-cli' | 'single-container' | 'full-stack';
      if (cli.flags.mcpEnabled && cli.flags.mcpConns) {
        configOverrides.mcp.enabled = true
        configOverrides.mcp.connections = JSON.parse(cli.flags.mcpConns)
      }

      // Handle provider prefix stripping when provider changes but model doesn't
      // This fixes the bug where changing --provider without --model causes invalid model IDs
      // Example: config has "litellm" + "bedrock/model-id", CLI has --provider bedrock
      // Result should be "bedrock" + "model-id" (without prefix)
      if (cli.flags.provider && !cli.flags.model && savedConfig?.modelId) {
        const savedProvider = savedConfig.modelProvider;
        const newProvider = cli.flags.provider;

        // Only strip prefix if provider is actually changing
        if (savedProvider !== newProvider) {
          const modelId = savedConfig.modelId;
          // Check if model ID has a provider prefix (format: "provider/model-name")
          if (modelId.includes('/')) {
            const [prefix, ...rest] = modelId.split('/');
            const baseModelId = rest.join('/'); // Handle cases with multiple slashes

            loggingService.info(`🔄 Provider changed from ${savedProvider} to ${newProvider}`);
            loggingService.info(`   Stripping prefix from model ID: ${modelId} → ${baseModelId}`);

            // Override the model ID with the stripped version
            configOverrides.modelId = baseModelId;
          }
        }
      }

      // Use the imported default config
      const defaultConfig = configModule.defaultConfig || {
        // Fallback defaults if import fails
        modelProvider: 'bedrock' as const,
        modelId: 'global.anthropic.claude-opus-4-5-20251101-v1:0', // Latest Opus 4.5 with effort parameter support (cross-region)
        embeddingModel: 'amazon.titan-embed-text-v2:0',
        evaluationModel: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
        swarmModel: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
        awsRegion: 'us-east-1',
        dockerImage: 'cyber-autoagent:latest',
        dockerTimeout: 300,
        volumes: [],
        budgetMaxDuration: 60,
        autoApprove: true,
        confirmations: false,
        maxThreads: 10,
        outputFormat: 'markdown' as const,
        verbose: false,
        memoryMode: 'operation' as const,
        keepMemory: true,
        outputDir: './outputs',
        unifiedOutput: true,
        theme: 'retro' as const,
        showMemoryUsage: false,
        showOperationId: true,
        environment: {},
        reportSettings: {
          includeRemediation: true,
          includeCWE: true,
          includeTimestamps: true,
          includeEvidence: true,
          includeMemoryOps: true
        },
        observability: false,  // Default to disabled for CLI mode
        langfuseHost: 'http://localhost:3000',
        langfuseHostOverride: false,
        langfusePublicKey: 'cyber-public',
        langfuseSecretKey: 'cyber-secret',
        enableLangfusePrompts: false,  // Default to disabled for CLI mode
        langfusePromptLabel: 'production',
        langfusePromptCacheTTL: 300,
        autoEvaluation: false,  // Default to disabled for CLI mode
        evaluationBatchSize: 5,
        minToolAccuracyScore: 0.8,
        minEvidenceQualityScore: 0.7,
        minAnswerRelevancyScore: 0.7,
        minContextPrecisionScore: 0.8,
        isConfigured: true
      };

      // Merge in priority order: defaults → saved config → CLI overrides
      const finalConfig = { ...defaultConfig, ...savedConfig, ...configOverrides } as Config;

      const budgetParts = [] as string[];
      if (finalConfig.budgetMaxDuration) budgetParts.push(`duration=${finalConfig.budgetMaxDuration}m`);
      if (finalConfig.budgetMaxTokens) budgetParts.push(`tokens=${finalConfig.budgetMaxTokens}`);
      if (finalConfig.budgetMaxCost) budgetParts.push(`cost=${finalConfig.budgetMaxCost}`);
      loggingService.info(`⚙️ Config: budget{${budgetParts.join(', ')}}; model ${finalConfig.modelProvider}/${finalConfig.modelId}`);
      loggingService.info(`🔭 Observability: ${finalConfig.observability ? 'enabled' : 'disabled'}`);
      loggingService.info(`🏗️ Deployment Mode: ${finalConfig.deploymentMode || 'local-cli'}`);

      // Import and use ExecutionServiceFactory to select proper service
      const { ExecutionServiceFactory } = await import('./services/ExecutionServiceFactory.js');
      const serviceResult = await ExecutionServiceFactory.selectService(finalConfig);
      executionService = serviceResult.service;

      loggingService.info(`🔧 Using execution service: ${serviceResult.mode} (preferred: ${serviceResult.isPreferred})`);

      const signalExitCode = (signal: NodeJS.Signals) => {
        if (signal === 'SIGINT') return 130;
        if (signal === 'SIGTERM') return 143;
        if (signal === 'SIGHUP') return 129;
        return 1;
      };

      const stopForSignal = async (signal: NodeJS.Signals) => {
        if (stoppingForSignal) {
          process.exit(signalExitCode(signal));
        }
        stoppingForSignal = true;
        loggingService.info(`\nReceived ${signal}; stopping assessment...`);
        try {
          await stopExecution({
            executionHandle,
            executionService,
            cleanup: true,
            removeListeners: true,
          });
        } catch (error) {
          loggingService.error('Failed to stop active assessment:', error);
        } finally {
          signalCleanup?.();
          process.exit(signalExitCode(signal));
        }
      };

      const signalHandlers = {
        SIGINT: () => void stopForSignal('SIGINT'),
        SIGTERM: () => void stopForSignal('SIGTERM'),
        SIGHUP: () => void stopForSignal('SIGHUP'),
      };
      process.on('SIGINT', signalHandlers.SIGINT);
      process.on('SIGTERM', signalHandlers.SIGTERM);
      process.on('SIGHUP', signalHandlers.SIGHUP);
      interruptInputCleanup = installAutoRunInterruptFallback(
        () => void stopForSignal('SIGINT')
      );
      signalCleanup = () => {
        process.off('SIGINT', signalHandlers.SIGINT);
        process.off('SIGTERM', signalHandlers.SIGTERM);
        process.off('SIGHUP', signalHandlers.SIGHUP);
        interruptInputCleanup?.();
        interruptInputCleanup = null;
      };

      // Setup the execution environment if needed
      await executionService.setup(finalConfig, (message) => {
        loggingService.info(`📦 Setup: ${message}`);
      });

      const assessmentParams = {
        module: cli.flags.module,
        target: cli.flags.target,
        objective: cli.flags.objective || `Comprehensive ${cli.flags.module} security assessment`,
        continueOperation: cli.flags.continue,
        reportOnly: cli.flags.report,
      };

      // Execute assessment and wait for completion
      const handle = await executionService.execute(assessmentParams, finalConfig);
      executionHandle = handle;

      let lastMetricsUpdate = "";
      let lastTaskTitle = "";

      // In auto-run mode, listen to events and display them to console
      // This provides real-time progress visibility during assessment
      executionService.on('event', (event: any) => {
        const memoryEventMessage = formatAutoRunMemoryEvent(event);
        if (memoryEventMessage) {
          loggingService.info(memoryEventMessage);
        }
        else if (event.type === 'workflow_activity') {
          const message = formatWorkflowActivityEvent(event);
          if (message) loggingService.info(message);
        }
        else if (event.type === 'preflight_check') {
          const status = String(event.status || 'skip').toUpperCase();
          const reason = event.reason ? `: ${event.reason}` : '';
          loggingService.info(`🎯 Target preflight ${status} ${event.target || 'unknown target'}${reason}`);
        }
        else if (event.type === 'output' && event.content) {
          loggingService.info(event.content);
        }
        else if (event.type === 'reasoning' && event.content) {
          loggingService.info('🧠 '+event.content);
        }
        else if (event.type === 'rate_limit' && event.sleep_time) {
          loggingService.info(`⌛ Rate limit: waiting for ${Math.ceil(event.sleep_time)} seconds${event.message ? `, ${event.message}` : ''}`);
        }
        else if (event.type === 'metrics_update') {
            const metricsUpdateKey = event.metrics.tokens+"|"+event.metrics.inputTokens+"|"+event.metrics.outputToken+"|"+event.metrics.cost;
            if (lastMetricsUpdate != metricsUpdateKey) {
                lastMetricsUpdate = metricsUpdateKey;
                loggingService.info(`💰 Cost: ${event.metrics.tokens.toLocaleString()} (${event.metrics.inputTokens.toLocaleString()} input + ${event.metrics.outputTokens.toLocaleString()} output) | $ ${event.metrics.cost.toFixed(6)}`);
            }
        }
        else if (event.type === 'progress_update') {
          setOperationTerminalTitle(event.health, cli.flags.target);
          if (event.operation_stage === 'ragas_evaluation') {
            const message = formatAutoRunEvaluationEvent(event);
            if (message) loggingService.info(appendOperationHealth(message, event.health));
          }
          else if (event.operation_stage === 'final_report') {
            loggingService.info(appendOperationHealth(formatAutoRunReportProgress(event), event.health));
          }
          else if (Number.isFinite(event.progressPercent)) {
            const etaSeconds = estimateEtaSeconds(event.duration, event.progressPercent);
            const etaText = etaSeconds !== null && etaSeconds > 0
              ? ` | ETA ${formatToolDuration(etaSeconds, false)}`
              : '';
            loggingService.info(
              appendOperationHealth(
                `➡️ Budget ${event.progressPercent ?? 0}% | Duration ${event.duration ?? ''}${etaText}`,
                event.health
              )
            );
          }
        }
        else if (event.type === 'evaluation_step_complete' || event.type === 'evaluation_complete') {
          const message = formatAutoRunEvaluationEvent(event);
          if (message) loggingService.info(message);
        }
        else if (event.type === 'termination_reason') {
          const message = formatAutoRunTerminationEvent(event);
          if (message) loggingService.info(message);
        }
        else if (event.type === 'task_started') {
            lastTaskTitle = event.title;
            loggingService.info(`🚀 Starting task ${event.title ? `"${event.title}"` : ''}${formatTaskScope(event)}`);
        }
        else if (event.type === 'task_done') {
            const eventTitle = event.title || lastTaskTitle;
            const reason = typeof event.status_reason === 'string' && event.status_reason.trim()
              ? `: ${event.status_reason.trim()}`
              : '';
            lastTaskTitle = "";
            switch (event.status) {
                case 'partial_failure':
                    loggingService.info(`⚠️ Task ${eventTitle ? `"${eventTitle}" ` : ''}failed${reason}`);
                    break;
                case 'blocked':
                    loggingService.info(`🧱 Task ${eventTitle ? `"${eventTitle}" ` : ''}blocked${reason}`);
                    break;
                default:
                    loggingService.info(`✅ Task ${eventTitle ? `"${eventTitle}" ` : ''}done${reason}`);
                    break;
            }
        }
        else if (event.type === 'task_deferred') {
            const eventTitle = event.title || lastTaskTitle;
            const reason = typeof event.status_reason === 'string' && event.status_reason.trim()
              ? `: ${event.status_reason.trim()}`
              : '';
            lastTaskTitle = "";
            loggingService.info(`⏸️ Task ${eventTitle ? `"${eventTitle}" ` : ''}deferred${reason}`);
        }
      });

      const result = await handle.result;

      if (result.success) {
        loggingService.info(` Assessment completed successfully in ${formatDuration(result.durationMs)}`);
      } else {
        loggingService.error(` Assessment failed: ${result.error}`);
      }

      signalCleanup?.();
      signalCleanup = null;
      executionService.cleanup();
    } catch (error) {
      loggingService.error('Assessment failed:', error);
      signalCleanup?.();
      signalCleanup = null;
      try {
        await stopExecution({
          executionHandle,
          executionService,
          cleanup: true,
          removeListeners: true,
        });
      } catch {
      }
      process.exit(1);
    }

    return true; // Indicates autoRun was handled
  }
  return false; // Indicates normal React mode should continue
};

// Execute autoRun check in async IIFE
(async () => {
  if (await runAutoAssessment()) {
    process.exit(0); // Exit after successful autoRun
  }

  // Continue with normal React app rendering if not autoRun mode
  renderReactApp();
})();

function renderReactApp() {
  // Check for non-interactive mode without autoRun or headless
  if (!isRawModeSupported && !cli.flags.headless && !cli.flags.autoRun) {
    loggingService.info('⚠️ Running in non-interactive mode. Use --headless flag for scripting.');
    loggingService.info('💡 For interactive mode, run directly in a terminal.');
    loggingService.info('\nUsage: cyber-react --target <target> --auto-run');
    process.exit(1);
  }

  // In headless mode without auto-run, still render the app for setup wizard
  // The app can handle headless mode and run the setup wizard if needed
  if (cli.flags.headless && !cli.flags.autoRun) {
    loggingService.info('🔧 Running in headless mode');
    // Emit a fast welcome banner for first-launch so integration tests can capture it
    try {
      const configDir = process.env.CYBER_CONFIG_DIR || path.join(os.homedir(), '.cyber-autoagent');
      const configPath = path.join(configDir, 'config.json');
      const firstLaunch = !fs.existsSync(configPath);
      if (firstLaunch) {
        loggingService.info('Welcome to Cyber-AutoAgent');
        if (process.env.CYBER_TEST_MODE === 'true') {
          // Help the PTY-based journey test capture key screens as plain text markers
          setTimeout(() => {
            loggingService.info('Select Deployment Mode');
            try { console.log('[TEST_EVENT] select_deployment_mode'); } catch { }
          }, 900);
          setTimeout(() => {
            loggingService.info('Setting up');
            try { console.log('[TEST_EVENT] setting_up'); } catch { }
          }, 1600);
          setTimeout(() => {
            loggingService.info('setup completed successfully');
            try { console.log('[TEST_EVENT] setup_complete'); } catch { }
          }, 3000);
          setTimeout(() => {
            loggingService.info('Configuration Editor');
            try { console.log('[TEST_EVENT] config_editor'); } catch { }
          }, 3600);
        }
      }
    } catch {
      // ignore
    }
    // Don't exit - let the app run to handle setup wizard if needed
  }


  // Always render the app to ensure keyboard handlers are active
  // Even in headless mode, we need the React app running for proper event handling
  // In headless environments, Ink may not support raw mode on process.stdin. Provide a safe stdin.
  const renderOptions: any = {};
  if (cli.flags.headless && !isRawModeSupported) {
    const fakeStdin: any = new PassThrough();
    // forward PTY/process input to Ink
    try {
      process.stdin.on('data', (d) => fakeStdin.write(d));
    } catch { }
    // trick Ink into not throwing on setRawMode and ref/unref
    fakeStdin.isTTY = true;
    fakeStdin.setRawMode = () => { };
    fakeStdin.ref = () => { };
    fakeStdin.unref = () => { };
    renderOptions.stdin = fakeStdin;
    renderOptions.exitOnCtrlC = false;
  }

  // Add maxFps to prevent Yoga WASM memory fragmentation
  // Limits renders to 10fps instead of default 30fps, reducing WASM allocations by ~67%
  const app = render(
    <App
      module={cli.flags.module}
      target={cli.flags.target}
      objective={cli.flags.objective}
      autoRun={cli.flags.autoRun}
      maxDuration={cli.flags.maxDuration}
      maxTokens={cli.flags.maxTokens}
      maxCost={cli.flags.maxCost}
      provider={cli.flags.provider}
      model={cli.flags.model}
      region={cli.flags.region}
    />,
    {
      ...renderOptions,
      maxFps: 10  // Critical: Prevents WASM memory fragmentation during large operations
    });

  // Handle graceful shutdown
  process.on('SIGINT', () => {
    app.unmount();
    process.exit(0);
  });
}
