/**
 * Modal Registry Component
 * 
 * Centralized component for rendering all modals based on the active modal type.
 * This ensures consistent modal behavior and simplifies the main App component.
 */

import React from 'react';
import { Box } from 'ink';
// Use lazy-loaded components for better performance
import { 
  ConfigEditorLazy,
  ModuleSelectorLazy,
  DocumentationViewerLazy 
} from './LazyComponents.js';
// These components are small enough to load directly
import { SafetyWarning } from './SafetyWarning.js';
import { InitializationFlow } from './InitializationFlow.js';
import { ModalType } from '../hooks/useModalManager.js';
import { AssessmentFlow } from '../services/AssessmentFlow.js';

interface ModalRegistryProps {
  activeModal: ModalType;
  modalContext: any;
  onClose: () => void;
  terminalWidth: number;
  
  // Additional props for specific modals
  assessmentFlowManager?: AssessmentFlow;
  addOperationHistoryEntry?: (type: any, content: string, operation?: any) => void;
  onSafetyConfirm?: () => void;
  isFirstRunExperience?: boolean;
  setIsFirstRunExperience?: (value: boolean) => void;
  setIsConfigurationModalOpen?: (value: boolean) => void;
}

const ModalWrapper: React.FC<{ children: React.ReactNode; terminalWidth: number }> = ({ children, terminalWidth }) => {
  const fallbackWidth = (process as any)?.stdout?.columns ?? 100;
  const width = Math.max(40, Math.floor(((terminalWidth || fallbackWidth) * 0.9)));

  return (
    <Box flexDirection="column" width={width}>
      {children}
    </Box>
  );
};

export const ModalRegistry: React.FC<ModalRegistryProps> = ({
  activeModal,
  modalContext,
  onClose,
  terminalWidth,
  assessmentFlowManager,
  addOperationHistoryEntry,
  onSafetyConfirm,
  isFirstRunExperience,
  setIsFirstRunExperience,
  setIsConfigurationModalOpen
}) => {
  // Don't render anything if no modal is active
  if (activeModal === ModalType.NONE) {
    return null;
  }
  
  switch (activeModal) {
    case ModalType.CONFIG:
      return (
        <ModalWrapper terminalWidth={terminalWidth}>
          <ConfigEditorLazy 
            onClose={() => {
              onClose();
              // Show welcome message after config if first run
              if (isFirstRunExperience) {
                addOperationHistoryEntry?.('info', 'Configuration complete! Type /help for commands or try: scan example.com');
                setIsFirstRunExperience?.(false);
              }
            }}
          />
        </ModalWrapper>
      );
      
    case ModalType.MEMORY_SEARCH:
      // Memory functionality is provided by the Python Qdrant integration.
      return null;
      
    case ModalType.MODULE_SELECTOR:
      return (
        <ModalWrapper terminalWidth={terminalWidth}>
          <ModuleSelectorLazy 
            onClose={onClose}
            onSelect={(moduleName) => {
              onClose();
              if (modalContext.onModuleSelect) {
                modalContext.onModuleSelect(moduleName);
              }
            }}
          />
        </ModalWrapper>
      );
      
    case ModalType.SAFETY_WARNING:
      return (
        <ModalWrapper terminalWidth={terminalWidth}>
          {modalContext.pendingExecution && (
            <SafetyWarning 
              target={modalContext.pendingExecution.target}
              module={modalContext.pendingExecution.module}
              onConfirm={() => {
                onClose();
                onSafetyConfirm?.();
              }}
              onCancel={onClose}
            />
          )}
        </ModalWrapper>
      );
      
    case ModalType.INITIALIZATION:
      return (
        <ModalWrapper terminalWidth={terminalWidth}>
          <InitializationFlow 
            onComplete={() => {
              onClose();
              // Show config editor after initialization
              setIsConfigurationModalOpen?.(true);
            }}
          />
        </ModalWrapper>
      );
      
    case ModalType.DOCUMENTATION:
      return (
        <ModalWrapper terminalWidth={terminalWidth}>
          <DocumentationViewerLazy 
            onClose={onClose}
            selectedDoc={modalContext.documentIndex}
          />
        </ModalWrapper>
      );
      
    default:
      return null;
  }
};
