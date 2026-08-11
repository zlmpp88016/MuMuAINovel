import { useEffect, useState } from 'react';
import { Alert, Button, Select, Space, Spin } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { settingsApi } from '../services/api';
import type { LLMConfiguration } from '../types';

interface LLMConfigSelectorProps {
  value?: string;
  onChange?: (value?: string) => void;
  placeholder?: string;
  size?: 'small' | 'middle' | 'large';
  disabled?: boolean;
}

/** A request-level selector; clearing it delegates to the module binding. */
export default function LLMConfigSelector({
  value,
  onChange,
  placeholder = '跟随模块默认配置',
  size = 'middle',
  disabled = false,
}: LLMConfigSelectorProps) {
  const [configs, setConfigs] = useState<LLMConfiguration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadConfigurations = async () => {
    setLoading(true);
    setError('');
    try {
      setConfigs(await settingsApi.getLLMConfigurations());
    } catch {
      setError('无法加载 LLM 配置');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadConfigurations();
  }, []);

  const enabledConfigs = configs.filter(config => config.enabled && config.api_key_configured);

  return (
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      <Space.Compact style={{ width: '100%' }}>
        <Select
          allowClear
          size={size}
          value={value}
          onChange={(nextValue) => onChange?.(nextValue || undefined)}
          placeholder={placeholder}
          loading={loading}
          disabled={disabled}
          style={{ width: '100%' }}
          options={enabledConfigs.map(config => ({
            value: config.id,
            label: `${config.name} · ${config.llm_model}`,
            title: `${config.api_provider} · ${config.api_key_masked}`,
          }))}
          notFoundContent={loading ? <Spin size="small" /> : '暂无可用配置'}
        />
        <Button
          icon={<ReloadOutlined />}
          aria-label="重新加载 LLM 配置"
          title="重新加载 LLM 配置"
          onClick={() => void loadConfigurations()}
          disabled={disabled || loading}
        />
      </Space.Compact>
      {error && (
        <Alert
          type="warning"
          showIcon
          message={error}
          action={(
            <Button type="link" size="small" onClick={() => void loadConfigurations()}>
              重试
            </Button>
          )}
        />
      )}
      {!loading && !error && enabledConfigs.length === 0 && (
        <Alert type="info" showIcon message="请先在设置中添加并启用 LLM 配置" />
      )}
    </Space>
  );
}
