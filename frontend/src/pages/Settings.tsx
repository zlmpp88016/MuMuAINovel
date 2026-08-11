import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { settingsApi } from '../services/api';
import type {
  LLMConfiguration,
  LLMConfigurationConnectionRequest,
  LLMConfigurationCreate,
  LLMConfigurationUpdate,
  LLMModuleBindingsResponse,
} from '../types';

const { Title, Text } = Typography;

const providerOptions = [
  { value: 'openai', label: 'OpenAI', baseUrl: 'https://api.openai.com/v1' },
  { value: 'anthropic', label: 'Anthropic', baseUrl: 'https://api.anthropic.com' },
  { value: 'deepseek', label: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1' },
  { value: 'siliconflow', label: 'SiliconFlow', baseUrl: 'https://api.siliconflow.cn/v1' },
  { value: 'moonshot', label: 'Moonshot', baseUrl: 'https://api.moonshot.cn/v1' },
  { value: 'qwen', label: 'Qwen / DashScope', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { value: 'custom', label: '自定义 OpenAI 兼容接口', baseUrl: '' },
];

interface ConfigFormValues {
  name: string;
  api_provider: string;
  api_key?: string;
  api_base_url?: string;
  llm_model: string;
  temperature: number;
  max_tokens: number;
  enabled: boolean;
  is_default: boolean;
}

export default function SettingsPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<ConfigFormValues>();
  const [configs, setConfigs] = useState<LLMConfiguration[]>([]);
  const [bindingData, setBindingData] = useState<LLMModuleBindingsResponse>({ modules: [], bindings: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<LLMConfiguration | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; response_time_ms?: number; response_preview?: string; error?: string } | null>(null);
  const [modelOptions, setModelOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [modelLoading, setModelLoading] = useState(false);
  const [bindingLoading, setBindingLoading] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const [nextConfigs, nextBindings] = await Promise.all([
        settingsApi.getLLMConfigurations(),
        settingsApi.getLLMBindings(),
      ]);
      setConfigs(nextConfigs);
      setBindingData(nextBindings);
    } catch {
      message.error('加载 LLM 配置失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  const bindingByModule = useMemo(
    () => new Map(bindingData.bindings.map(binding => [binding.module_key, binding.llm_config_id])),
    [bindingData.bindings],
  );

  const openCreate = () => {
    setEditing(null);
    setModelOptions([]);
    setTestResult(null);
    form.setFieldsValue({
      name: '',
      api_provider: 'openai',
      api_key: '',
      api_base_url: 'https://api.openai.com/v1',
      llm_model: 'gpt-4o-mini',
      temperature: 0.7,
      max_tokens: 2000,
      enabled: true,
      is_default: configs.length === 0,
    });
    setModalOpen(true);
  };

  const openEdit = (config: LLMConfiguration) => {
    setEditing(config);
    setModelOptions([{ value: config.llm_model, label: config.llm_model }]);
    setTestResult(null);
    form.setFieldsValue({
      name: config.name,
      api_provider: config.api_provider,
      api_key: '',
      api_base_url: config.api_base_url || '',
      llm_model: config.llm_model,
      temperature: config.temperature,
      max_tokens: config.max_tokens,
      enabled: config.enabled,
      is_default: config.is_default,
    });
    setModalOpen(true);
  };

  const handleProviderChange = (provider: string) => {
    const option = providerOptions.find(item => item.value === provider);
    if (option?.baseUrl) {
      form.setFieldValue('api_base_url', option.baseUrl);
    }
    setModelOptions([]);
  };

  const handleFetchModels = async () => {
    const values = form.getFieldsValue();
    const apiKey = values.api_key?.trim();
    if (!editing && !apiKey) {
      message.warning('请先填写 API Key');
      return;
    }
    if (!values.api_base_url && !editing) {
      message.warning('请先填写 API Base URL');
      return;
    }

    setModelLoading(true);
    try {
      const result = await settingsApi.getAvailableModels(
        editing && !apiKey
          ? { config_id: editing.id }
          : {
              api_key: apiKey,
              api_base_url: values.api_base_url,
              provider: values.api_provider,
            },
      );
      setModelOptions(result.models.map(model => ({ value: model.value, label: model.label })));
      message.success(`已获取 ${result.count || result.models.length} 个模型`);
    } catch {
      setModelOptions([]);
      message.error('获取模型列表失败，请手动填写模型名称');
    } finally {
      setModelLoading(false);
    }
  };

  const handleTest = async () => {
    try {
      const values = await form.validateFields(['api_provider', 'api_key', 'api_base_url', 'llm_model']);
      const request: LLMConfigurationConnectionRequest = {
        api_provider: values.api_provider,
        api_key: values.api_key || '',
        api_base_url: values.api_base_url,
        llm_model: values.llm_model,
        temperature: values.temperature,
        max_tokens: 100,
      };
      setTesting(true);
      const result = editing && !values.api_key
        ? await settingsApi.testSavedLLMConfiguration(editing.id)
        : await settingsApi.testLLMConfiguration(request);
      setTestResult(result);
      if (result.success) {
        message.success('API 连接测试成功');
      }
    } catch {
      message.error('API 连接测试失败');
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async (values: ConfigFormValues) => {
    setSaving(true);
    try {
      if (editing) {
        const payload: LLMConfigurationUpdate = {
          name: values.name,
          api_provider: values.api_provider,
          api_base_url: values.api_base_url,
          llm_model: values.llm_model,
          temperature: values.temperature,
          max_tokens: values.max_tokens,
          enabled: values.enabled,
          is_default: values.is_default,
        };
        if (values.api_key?.trim()) payload.api_key = values.api_key.trim();
        await settingsApi.updateLLMConfiguration(editing.id, payload);
        message.success('LLM 配置已更新');
      } else {
        const payload: LLMConfigurationCreate = {
          ...values,
          api_key: values.api_key?.trim() || '',
          name: values.name.trim(),
          llm_model: values.llm_model.trim(),
        };
        await settingsApi.createLLMConfiguration(payload);
        message.success('LLM 配置已添加');
      }
      setModalOpen(false);
      await loadData();
    } catch {
      message.error('保存 LLM 配置失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (config: LLMConfiguration) => {
    try {
      await settingsApi.deleteLLMConfiguration(config.id);
      message.success('LLM 配置已删除');
      await loadData();
    } catch {
      message.error('删除 LLM 配置失败');
    }
  };

  const handleBindingChange = async (moduleKey: string, configId?: string) => {
    setBindingLoading(moduleKey);
    try {
      await settingsApi.updateLLMBindings({ [moduleKey]: configId || null });
      setBindingData(await settingsApi.getLLMBindings());
      message.success('模块模型已更新');
    } catch {
      message.error('模块模型更新失败');
    } finally {
      setBindingLoading(null);
    }
  };

  const enabledConfigs = configs.filter(config => config.enabled && config.api_key_configured);

  return (
    <div style={{ minHeight: '100%', background: '#f5f7fa', padding: 24 }}>
      <div style={{ maxWidth: 1180, margin: '0 auto' }}>
        <Space direction="vertical" size={20} style={{ width: '100%' }}>
          <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space>
              <Button icon={<ArrowLeftOutlined />} type="text" onClick={() => navigate('/')} aria-label="返回首页" />
              <Title level={2} style={{ margin: 0 }}><SettingOutlined /> AI 配置中心</Title>
            </Space>
            <Button icon={<ReloadOutlined />} onClick={() => void loadData()} loading={loading}>刷新</Button>
          </Space>

          <Alert
            type="info"
            showIcon
            message="多配置与模块路由"
            description="新增的配置可分别绑定到世界观、大纲、章节、角色、组织和润色模块。生成请求也可以临时覆盖模块默认配置；未选择时继续使用旧 Settings 作为兜底。"
          />

          <Card
            title="LLM API 配置"
            extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增配置</Button>}
          >
            <Spin spinning={loading}>
              {configs.length === 0 ? (
                <Empty description="还没有 LLM 配置" />
              ) : (
                <Table
                  rowKey="id"
                  pagination={false}
                  dataSource={configs}
                  columns={[
                    { title: '名称', dataIndex: 'name', key: 'name', render: (value: string, config: LLMConfiguration) => <Space>{value}{config.is_default && <Tag color="blue">默认</Tag>}</Space> },
                    { title: '提供商 / 模型', key: 'model', render: (_: unknown, config: LLMConfiguration) => <div><div>{config.api_provider}</div><Text type="secondary">{config.llm_model}</Text></div> },
                    { title: 'API Key', dataIndex: 'api_key_masked', key: 'api_key', render: (value: string) => value || <Text type="danger">未配置</Text> },
                    { title: '状态', dataIndex: 'enabled', key: 'enabled', render: (enabled: boolean) => enabled ? <Tag color="success">启用</Tag> : <Tag>停用</Tag> },
                    { title: '操作', key: 'actions', width: 150, render: (_: unknown, config: LLMConfiguration) => <Space><Button icon={<EditOutlined />} onClick={() => openEdit(config)} aria-label={`编辑 ${config.name}`} /><Popconfirm title="删除此 LLM 配置？" description="已绑定的模块会自动解除绑定。" onConfirm={() => void handleDelete(config)}><Button danger icon={<DeleteOutlined />} aria-label={`删除 ${config.name}`} /></Popconfirm></Space> },
                  ]}
                />
              )}
            </Spin>
          </Card>

          <Card title="模块默认模型" extra={<Text type="secondary">清空后按默认配置或旧 Settings 解析</Text>}>
            {bindingData.modules.length === 0 ? (
              <Empty description="暂无模块信息" />
            ) : (
              <Row gutter={[16, 16]}>
                {bindingData.modules.map(module => (
                  <Col xs={24} sm={12} lg={8} key={module.key}>
                    <Space direction="vertical" size={6} style={{ width: '100%' }}>
                      <Text strong>{module.label}</Text>
                      <Select
                        allowClear
                        loading={bindingLoading === module.key}
                        value={bindingByModule.get(module.key)}
                        placeholder="不绑定，使用系统默认"
                        options={enabledConfigs.map(config => ({ value: config.id, label: `${config.name} · ${config.llm_model}` }))}
                        onChange={(value) => void handleBindingChange(module.key, value)}
                        style={{ width: '100%' }}
                        notFoundContent="暂无启用配置"
                      />
                    </Space>
                  </Col>
                ))}
              </Row>
            )}
          </Card>
        </Space>
      </div>

      <Modal
        open={modalOpen}
        title={editing ? '编辑 LLM 配置' : '新增 LLM 配置'}
        width={720}
        destroyOnClose
        onCancel={() => setModalOpen(false)}
        footer={null}
      >
        <Form form={form} layout="vertical" onFinish={handleSave} style={{ marginTop: 20 }}>
          <Row gutter={16}>
            <Col xs={24} md={12}><Form.Item name="name" label="配置名称" rules={[{ required: true, message: '请输入配置名称' }]}><Input placeholder="例如：主力写作模型" /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name="api_provider" label="提供商" rules={[{ required: true }]}><Select options={providerOptions} onChange={handleProviderChange} /></Form.Item></Col>
          </Row>
          <Form.Item name="api_key" label="API Key" rules={editing ? [] : [{ required: true, message: '请输入 API Key' }]} extra={editing ? '留空表示保留现有 Key；服务端不会返回明文 Key。' : 'Key 仅用于服务端调用和连接测试。'}><Input.Password autoComplete="new-password" placeholder={editing ? '留空保持不变' : '输入 API Key'} /></Form.Item>
          <Form.Item name="api_base_url" label="API Base URL" rules={[{ required: true, message: '请输入 API Base URL' }]}><Input placeholder="https://api.openai.com/v1" /></Form.Item>
          <Form.Item label="模型" required>
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="llm_model" noStyle rules={[{ required: true, message: '请输入模型名称' }]}>
                <AutoComplete
                  style={{ width: '100%' }}
                  options={modelOptions}
                  placeholder="输入模型名称"
                  onSelect={(value) => form.setFieldValue('llm_model', value)}
                />
              </Form.Item>
              <Button icon={<ReloadOutlined />} onClick={() => void handleFetchModels()} loading={modelLoading}>获取模型</Button>
            </Space.Compact>
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} md={12}><Form.Item name="temperature" label="Temperature"><Slider min={0} max={2} step={0.1} marks={{ 0: '0', 1: '1', 2: '2' }} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name="max_tokens" label="最大 Tokens" rules={[{ required: true }]}><InputNumber min={1} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                name="enabled"
                label="启用"
                valuePropName="checked"
                tooltip="关闭后该配置立即失效：无法绑定到模块，也不会作为默认配置被选中。配置本身不会删除，可随时重新启用。"
                extra="关闭后配置立即失效"
              >
                <Switch />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="is_default"
                label="用户默认"
                valuePropName="checked"
                tooltip="生成请求既未临时指定 LLM 配置、也未给模块绑定配置时，使用这条默认配置兜底。遵循「请求指定 > 模块绑定 > 用户默认」的优先级。同一用户仅能有一个默认。"
                extra="兜底模型，未指定模块时使用"
              >
                <Switch />
              </Form.Item>
            </Col>
          </Row>

          {testResult && <Alert type={testResult.success ? 'success' : 'error'} showIcon icon={testResult.success ? <CheckCircleOutlined /> : undefined} message={testResult.message} description={testResult.success ? `${testResult.response_time_ms || 0} ms${testResult.response_preview ? ` · ${testResult.response_preview}` : ''}` : testResult.error} closable onClose={() => setTestResult(null)} style={{ marginBottom: 16 }} />}
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
            <Button icon={<ThunderboltOutlined />} onClick={() => void handleTest()} loading={testing}>测试连接</Button>
            <Button onClick={() => setModalOpen(false)}>取消</Button>
            <Button type="primary" icon={<SaveOutlined />} htmlType="submit" loading={saving}>保存配置</Button>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
