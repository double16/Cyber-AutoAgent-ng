import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';

import { useConfig } from '../contexts/ConfigContext.js';
import {
  CredentialInventory,
  CredentialManagementService,
  CredentialRecord,
  CredentialRotationRequest,
} from '../services/CredentialManagementService.js';
import { themeManager } from '../themes/theme-manager.js';

interface CredentialManagerProps {
  initialTarget?: string;
  onClose: () => void;
  onStartRotation?: (request: CredentialRotationRequest, objective: string, target: string) => void;
  service?: CredentialManagementService;
}

export const CredentialManager: React.FC<CredentialManagerProps> = ({ initialTarget = '', onClose, onStartRotation, service }) => {
  const { config } = useConfig();
  const manager = useMemo(() => service || new CredentialManagementService(), [service]);
  const theme = themeManager.getCurrentTheme();
  const [target, setTarget] = useState(initialTarget);
  const [inventory, setInventory] = useState<CredentialInventory>({ credentials: [] });
  const [selected, setSelected] = useState(0);
  const [reason, setReason] = useState('');
  const [mode, setMode] = useState<'target' | 'list' | 'reason' | 'cancel'>('target');
  const [statusFilter, setStatusFilter] = useState<'all' | 'valid' | 'invalid'>('all');
  const [message, setMessage] = useState('Enter a resolved target to review credential metadata.');

  const refresh = useCallback(async () => {
    if (!target.trim()) return;
    setMessage('Loading credential metadata…');
    try {
      const result = await manager.list(target.trim(), config);
      setInventory(result);
      setSelected(0);
      setMode('list');
      setMessage(result.credentials.length ? 'Select a credential; press r to queue rotation.' : 'No credentials found.');
    } catch (error) {
      setMessage(`Unable to load credentials: ${error instanceof Error ? error.message : String(error)}`);
    }
  }, [config, manager, target]);

  useEffect(() => {
    if (initialTarget) void refresh();
    // The selected target controls initial loading. Refreshes after that are explicit so configuration state changes
    // do not repeatedly query the store while the modal is rendering.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTarget]);

  const visibleCredentials = inventory.credentials.filter((credential) => {
    if (statusFilter === 'all') return true;
    return credential.status === statusFilter;
  });
  const selectedCredential: CredentialRecord | undefined = visibleCredentials[selected];
  useInput((input, key) => {
    if (key.escape) {
      onClose();
      return;
    }
    if (mode === 'target') {
      if (key.return) void refresh();
      return;
    }
    if (mode === 'reason' || mode === 'cancel') {
      if (key.return && selectedCredential && reason.trim()) {
        const queuedRequest = selectedCredential.rotation_requests.find((request) => request.status === 'queued');
        const action = mode === 'reason'
          ? manager.queueRotation(target.trim(), selectedCredential.credential_id, reason.trim(), config)
          : queuedRequest
            ? manager.cancelRotation(target.trim(), queuedRequest.request_id, reason.trim(), config)
            : Promise.reject(new Error('No queued rotation request is available to cancel'));
        void action
          .then((result) => {
            setReason('');
            setMode('list');
            const successMessage = mode === 'reason'
              ? `Queued maintenance operation ${result.request.maintenance_operation_id}.`
              : `Cancelled rotation request ${result.request.request_id}.`;
            return refresh().then(() => setMessage(successMessage));
          })
          .catch((error) => setMessage(`Rotation request failed: ${error instanceof Error ? error.message : String(error)}`));
      }
      return;
    }
    if (key.upArrow) setSelected((value) => Math.max(0, value - 1));
    if (key.downArrow) setSelected((value) => Math.min(Math.max(0, visibleCredentials.length - 1), value + 1));
    if (input === 'l') void refresh();
    if (input === 'f') {
      setStatusFilter((value) => value === 'all' ? 'valid' : value === 'valid' ? 'invalid' : 'all');
      setSelected(0);
    }
    if (input === 'r' && selectedCredential) {
      if (selectedCredential.management_policy !== 'operation') {
        setMessage('User-provided credentials must be changed through configuration/import.');
      } else {
        setMode('reason');
        setMessage('Explain why the authorized maintenance operation should rotate this credential.');
      }
    }
    if (input === 'c' && selectedCredential?.rotation_requests.some((request) => request.status === 'queued')) {
      setMode('cancel');
      setMessage('Explain why this queued rotation request should be cancelled.');
    }
    if (input === 's') {
      const request = selectedCredential?.rotation_requests.find((item) => item.status === 'queued');
      if (!request) {
        setMessage('Select a credential with a queued rotation request to start maintenance.');
      } else if (!onStartRotation) {
        setMessage('Rotation start is unavailable in this interface.');
      } else {
        void manager.startRotation(target.trim(), request.request_id, config)
          .then((result) => {
            onStartRotation(result.request, result.maintenance_objective, target.trim());
            setMessage(`Launching maintenance operation ${result.request.maintenance_operation_id}.`);
          })
          .catch((error) => setMessage(`Rotation start failed: ${error instanceof Error ? error.message : String(error)}`));
      }
    }
  });

  return (
    <Box flexDirection="column" padding={1} borderStyle="round" borderColor={theme.primary}>
      <Text color={theme.primary}>Credential Manager</Text>
      <Text dimColor>Secrets are never displayed. Target means the resolved target, never an operation target ID.</Text>
      {mode === 'target' && (
        <Box><Text>Target: </Text><TextInput value={target} onChange={setTarget} /></Box>
      )}
      {(mode === 'reason' || mode === 'cancel') && (
        <Box><Text>{mode === 'reason' ? 'Rotation reason: ' : 'Cancellation reason: '}</Text><TextInput value={reason} onChange={setReason} /></Box>
      )}
      {mode === 'list' && visibleCredentials.map((credential, index) => (
        <Box key={credential.credential_id} flexDirection="column">
          <Text color={index === selected ? theme.primary : undefined}>
            {index === selected ? '› ' : '  '}{credential.credential_type} {credential.role || 'unassigned'}
            {' '}[{credential.status}] {credential.origin}/{credential.management_policy}
          </Text>
          {index === selected && <Box flexDirection="column"><Text dimColor>
            target: {credential.target || 'mailbox credential'} · scope: {credential.operation_id || 'target-wide'}
          </Text><Text dimColor>
            account: {credential.account_label || 'unlabelled'} · tenant: {credential.tenant_label || 'unlabelled'} · invalidated: {credential.invalidated_at || 'never'}
          </Text><Text dimColor>
            lineage: {credential.supersedes_credential_id || 'original'} · history: {credential.history.map((event) => `${event.status} (${event.reason || event.created_at})`).join('; ') || 'none'}
          </Text><Text dimColor>
            rotations: {credential.rotation_requests.map((request) => `${request.status} ${request.request_id} (${request.reason})`).join('; ') || 'none'}
          </Text></Box>}
        </Box>
      ))}
      <Text color={theme.muted}>{message}</Text>
      <Text dimColor>{mode === 'list' ? `filter: ${statusFilter} · ↑/↓ select · r queue · s start queued · c cancel queued · f filter · l refresh · Esc close` : 'Enter confirm · Esc close'}</Text>
    </Box>
  );
};
