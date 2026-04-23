import React, { useState, useEffect } from 'react';
import {
  Typography, Card, Tabs, Descriptions, Form, InputNumber,
  Select, Button, Space, Badge, message, Spin, List, Tag, Popconfirm,
} from 'antd';
import {
  ReloadOutlined, SaveOutlined, WifiOutlined,
  SettingOutlined, HeartOutlined, PoweroffOutlined,
} from '@ant-design/icons';
import api from '../../api';

const { Title } = Typography;

interface ServiceStatus {
  name: string;
  status: string;
  description?: string;
}

export default function SystemSettings() {
  // RADIUS tab
  const [radiusSettings, setRadiusSettings] = useState<Record<string, any>>({});
  const [radiusLoading, setRadiusLoading] = useState(false);

  // General tab
  const [generalLoading, setGeneralLoading] = useState(false);
  const [generalSaving, setGeneralSaving] = useState(false);
  const [generalForm] = Form.useForm();

  // Service Status tab
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [servicesLoading, setServicesLoading] = useState(false);
  const [restartingService, setRestartingService] = useState<string | null>(null);

  const RESTARTABLE = ['gateway', 'freeradius', 'discovery', 'device_inventory', 'policy_engine', 'switch_mgmt', 'coa'];

  const handleRestart = async (serviceName: string) => {
    setRestartingService(serviceName);
    try {
      await api.post(`/settings/service-restart/${serviceName}`);
      message.success(`${serviceName} restart requested`);
      if (serviceName === 'gateway') {
        message.info('Gateway is restarting, please wait...');
        setTimeout(() => { loadServiceStatus(); }, 8000);
      } else {
        setTimeout(() => { loadServiceStatus(); }, 3000);
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || `Failed to restart ${serviceName}`);
    } finally {
      setRestartingService(null);
    }
  };

  useEffect(() => {
    loadRadiusSettings();
    loadGeneralSettings();
    loadServiceStatus();
  }, []);

  const loadRadiusSettings = async () => {
    setRadiusLoading(true);
    try {
      const res = await api.get('/settings/radius');
      const data = res.data || {};
      // Backend returns { category, settings: [{setting_key, setting_value}, ...] }
      if (data.settings && Array.isArray(data.settings)) {
        const map: Record<string, any> = {};
        data.settings.forEach((s: any) => { map[s.setting_key] = s.setting_value; });
        setRadiusSettings(map);
      } else {
        setRadiusSettings(data);
      }
    } catch { message.error('Failed to load RADIUS settings'); }
    setRadiusLoading(false);
  };

  const loadGeneralSettings = async () => {
    setGeneralLoading(true);
    try {
      const res = await api.get('/settings/general');
      const data = res.data || {};
      // Backend returns { category, settings: [{setting_key, setting_value}, ...] }
      let map: Record<string, any> = {};
      if (data.settings && Array.isArray(data.settings)) {
        data.settings.forEach((s: any) => { map[s.setting_key] = s.setting_value; });
      } else {
        map = data;
      }
      generalForm.setFieldsValue({
        jwt_expire_minutes: map.jwt_expire_minutes ? Number(map.jwt_expire_minutes) : undefined,
        log_level: map.log_level,
        session_timeout_minutes: map.session_timeout_minutes ? Number(map.session_timeout_minutes) : undefined,
        max_login_attempts: map.max_login_attempts ? Number(map.max_login_attempts) : undefined,
      });
    } catch { message.error('Failed to load general settings'); }
    setGeneralLoading(false);
  };

  const handleSaveGeneral = async () => {
    try {
      const values = await generalForm.validateFields();
      setGeneralSaving(true);
      // Backend expects { settings: { key: value } }
      const settingsMap: Record<string, string> = {};
      Object.entries(values).forEach(([k, v]) => { settingsMap[k] = String(v); });
      await api.put('/settings/general', { settings: settingsMap });
      message.success('Settings saved');
    } catch (err: any) {
      if (err?.response?.data?.detail) {
        message.error(err.response.data.detail);
      }
    } finally {
      setGeneralSaving(false);
    }
  };

  const loadServiceStatus = async () => {
    setServicesLoading(true);
    try {
      const res = await api.get('/settings/service-status');
      const data = res.data;
      if (Array.isArray(data)) {
        setServices(data);
      } else if (data?.services) {
        setServices(data.services);
      } else {
        // Convert object format { service_name: { status, description } }
        const list: ServiceStatus[] = Object.entries(data).map(([name, info]: [string, any]) => ({
          name,
          status: typeof info === 'string' ? info : info?.status || 'unknown',
          description: typeof info === 'object' ? info?.description : undefined,
        }));
        setServices(list);
      }
    } catch {
      // Fallback: try /health endpoint
      try {
        const res = await api.get('/health');
        const data = res.data;
        if (data?.services) {
          setServices(data.services);
        } else {
          const list: ServiceStatus[] = Object.entries(data).map(([name, info]: [string, any]) => ({
            name,
            status: typeof info === 'string' ? info : info?.status || 'unknown',
            description: typeof info === 'object' ? info?.description : undefined,
          }));
          setServices(list);
        }
      } catch { message.error('Failed to load settings'); }
    }
    setServicesLoading(false);
  };

  const tabItems = [
    {
      key: 'radius',
      label: (
        <Space><WifiOutlined />RADIUS</Space>
      ),
      children: (
        <Spin spinning={radiusLoading}>
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="Auth Port">
              {radiusSettings.auth_port ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Acct Port">
              {radiusSettings.acct_port ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="CoA Port">
              {radiusSettings.coa_port ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Default EAP Type">
              {radiusSettings.default_eap_type ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="TLS Min Version">
              {radiusSettings.tls_min_version ?? '-'}
            </Descriptions.Item>
          </Descriptions>
          <div style={{ marginTop: 12, color: '#999', fontSize: 12 }}>
            RADIUS port settings are read-only. Changing ports requires a container restart.
          </div>
        </Spin>
      ),
    },
    {
      key: 'general',
      label: (
        <Space><SettingOutlined />General</Space>
      ),
      children: (
        <Spin spinning={generalLoading}>
          <Form
            form={generalForm}
            layout="vertical"
            style={{ maxWidth: 500 }}
          >
            <Form.Item
              name="jwt_expire_minutes"
              label="JWT Token Expiry (minutes)"
              rules={[{ required: true, message: 'Required' }]}
            >
              <InputNumber min={5} max={10080} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="log_level"
              label="Log Level"
              rules={[{ required: true, message: 'Required' }]}
            >
              <Select>
                <Select.Option value="DEBUG">DEBUG</Select.Option>
                <Select.Option value="INFO">INFO</Select.Option>
                <Select.Option value="WARNING">WARNING</Select.Option>
                <Select.Option value="ERROR">ERROR</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item
              name="session_timeout_minutes"
              label="Session Timeout (minutes)"
              rules={[{ required: true, message: 'Required' }]}
            >
              <InputNumber min={5} max={1440} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="max_login_attempts"
              label="Max Login Attempts"
              rules={[{ required: true, message: 'Required' }]}
            >
              <InputNumber min={1} max={100} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                onClick={handleSaveGeneral}
                loading={generalSaving}
              >
                Save Settings
              </Button>
            </Form.Item>
          </Form>
        </Spin>
      ),
    },
    {
      key: 'service-status',
      label: (
        <Space><HeartOutlined />Service Status</Space>
      ),
      children: (
        <>
          <Space style={{ marginBottom: 16 }}>
            <Button icon={<ReloadOutlined />} onClick={loadServiceStatus}>
              Refresh
            </Button>
          </Space>
          <List
            loading={servicesLoading}
            dataSource={services}
            grid={{ gutter: 16, column: 3 }}
            renderItem={(svc) => {
              const isUp = ['up', 'healthy', 'ok', 'running', 'connected'].includes(
                svc.status?.toLowerCase()
              );
              const canRestart = RESTARTABLE.includes(svc.name);
              return (
                <List.Item>
                  <Card size="small">
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                        <Space>
                          <Badge status={isUp ? 'success' : 'error'} />
                          <strong>{svc.name}</strong>
                        </Space>
                        {canRestart && (
                          <Popconfirm
                            title={`Restart ${svc.name}?`}
                            onConfirm={() => handleRestart(svc.name)}
                          >
                            <Button
                              size="small"
                              icon={<PoweroffOutlined />}
                              loading={restartingService === svc.name}
                              danger
                            >
                              Restart
                            </Button>
                          </Popconfirm>
                        )}
                      </Space>
                      <Tag color={isUp ? 'green' : 'red'}>{svc.status}</Tag>
                      {svc.description && (
                        <div style={{ fontSize: 12, color: '#666' }}>{svc.description}</div>
                      )}
                    </Space>
                  </Card>
                </List.Item>
              );
            }}
          />
        </>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>System Settings</Title>
      <Card>
        <Tabs items={tabItems} />
      </Card>
    </div>
  );
}
