import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';

import { useConfig } from '../contexts/ConfigContext.js';
import {
  CredentialInventory,
  CredentialManagementService,
  CredentialRecord,
} from '../services/CredentialManagementService.js';
import { themeManager } from '../themes/theme-manager.js';

interface CredentialManagerProps {
  initialTarget?: string;
  onClose: () => void;
  service?: CredentialManagementService;
}

export const CredentialManager: React.FC<CredentialManagerProps> = ({ initialTarget = '', onClose, service }) => {
  const { config } = useConfig();
  const manager = useMemo(() => service || new CredentialManagementService(), [service]);
  const theme = themeManager.getCurrentTheme();
  const [target, setTarget] = useState(initialTarget);
  const [inventory, setInventory] = useState<CredentialInventory>({ credentials: [] });
  const [selected, setSelected] = useState(0);
  const [reason, setReason] = useState('');
  const [mode, setMode] = useState<'target' | 'list' | 'reason'>('target');
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

  const selectedCredential: CredentialRecord | undefined = inventory.credentials[selected];
  useInput((input, key) => {
    if (key.escape) {
      onClose();
      return;
    }
    if (mode === 'target') {
      if (key.return) void refresh();
      return;
    }
    if (mode === 'reason') {
      if (key.return && selectedCredential && reason.trim()) {
        void manager.queueRotation(target.trim(), selectedCredential.credential_id, reason.trim(), config)
          .then((result) => {
            setReason('');
            setMode('list');
            setMessage(`Queued maintenance operation ${result.request.maintenance_operation_id}.`);
            return refresh();
          })
          .catch((error) => setMessage(`Rotation request failed: ${error instanceof Error ? error.message : String(error)}`));
      }
      return;
    }
    if (key.upArrow) setSelected((value) => Math.max(0, value - 1));
    if (key.downArrow) setSelected((value) => Math.min(Math.max(0, inventory.credentials.length - 1), value + 1));
    if (input === 'l') void refresh();
    if (input === 'r' && selectedCredential) {
      if (selectedCredential.management_policy !== 'operation') {
        setMessage('User-provided credentials must be changed through configuration/import.');
      } else {
        setMode('reason');
        setMessage('Explain why the authorized maintenance operation should rotate this credential.');
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
      {mode === 'reason' && (
        <Box><Text>Rotation reason: </Text><TextInput value={reason} onChange={setReason} /></Box>
      )}
      {mode === 'list' && inventory.credentials.map((credential, index) => (
        <Box key={credential.credential_id} flexDirection="column">
          <Text color={index === selected ? theme.primary : undefined}>
            {index === selected ? '› ' : '  '}{credential.credential_type} {credential.role || 'unassigned'}
            {' '}[{credential.status}] {credential.origin}/{credential.management_policy}
          </Text>
          {index === selected && <Text dimColor>
            {credential.target || 'mailbox credential'} · history {credential.history.length} · rotations {credential.rotation_requests.length}
          </Text>}
        </Box>
      ))}
      <Text color={theme.muted}>{message}</Text>
      <Text dimColor>{mode === 'list' ? '↑/↓ select · r queue rotation · l refresh · Esc close' : 'Enter confirm · Esc close'}</Text>
    </Box>
  );
};
