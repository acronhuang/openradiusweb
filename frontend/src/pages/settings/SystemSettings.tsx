import React, { useState, useEffect } from 'react';
import {
  Typography, Card, Tabs, Form, InputNumber,
  Select, Button, Space, Badge, message, Spin, List, Tag, Popconfirm,
} from 'antd';
import {
  ReloadOutlined, SaveOutlined, WifiOutlined,
  SettingOutlined, HeartOutlined, PoweroffOutlined,
} from '@ant-design/icons';
import api, { extractErrorMessage } from '../../api';

const { Title } = Typography;

interface ServiceStatus {
  name: string;
  status: string;
  description?: string;
}

export default function SystemSettings() {
  // RADIUS tab
  const [radiusLoading, setRadiusLoading] = useState(false);
  const [radiusSaving, setRadiusSaving] = useState(false);
  const [radiusForm] = Form.useForm();
  // Watched form value for the TLS 1.3 warning (re-renders when changed)
  const tlsMaxWatched = Form.useWatch('tls_max_version', radiusForm);

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
    } catch (err) {
      message.error(extractErrorMessage(err, `Failed to restart ${serviceName}`));
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
      let map: Record<string, any> = {};
      if (data.settings && Array.isArray(data.settings)) {
        data.settings.forEach((s: any) => { map[s.setting_key] = s.setting_value; });
      } else {
        map = data;
      }
      radiusForm.setFieldsValue({
        auth_port: map.auth_port ? Number(map.auth_port) : undefined,
        acct_port: map.acct_port ? Number(map.acct_port) : undefined,
        coa_port: map.coa_port ? Number(map.coa_port) : undefined,
        default_eap_type: map.default_eap_type || 'peap',
        // Defaults match what migrations/002 + 005 seed
        tls_min_version: map.tls_min_version || '1.2',
        tls_max_version: map.tls_max_version || '1.2',
      });
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load RADIUS settings')); }
    setRadiusLoading(false);
  };

  const handleSaveRadius = async () => {
    try {
      const values = await radiusForm.validateFields();
      // tls_max < tls_min would silently break EAP — block at form level
      const versions = ['1.0', '1.1', '1.2', '1.3'];
      if (versions.indexOf(values.tls_max_version) < versions.indexOf(values.tls_min_version)) {
        message.error('TLS Max Version must be ≥ TLS Min Version');
        return;
      }
      setRadiusSaving(true);
      const settingsMap: Record<string, string> = {};
      Object.entries(values).forEach(([k, v]) => { settingsMap[k] = String(v); });
      await api.put('/settings/radius', { settings: settingsMap });
      message.success(
        'RADIUS settings saved. TLS / EAP changes apply on next freeradius config regeneration ' +
        '(within ~30s via watcher, or restart freeradius to force).'
      );
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to save RADIUS settings'));
    } finally {
      setRadiusSaving(false);
    }
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
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load general settings')); }
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
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to save settings'));
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
      } catch (err) { message.error(extractErrorMessage(err, 'Failed to load settings')); }
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
          <Form
            form={radiusForm}
            layout="vertical"
            style={{ maxWidth: 500 }}
          >
            <Form.Item
              name="auth_port"
              label="Auth Port"
              extra="UDP port freeradius listens on for Access-Request packets. Default 1812. Changing requires a freeradius container restart."
              rules={[{ required: true, message: 'Required' }]}
            >
              <InputNumber min={1} max={65535} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="acct_port"
              label="Acct Port"
              extra="UDP port for Accounting-Request packets. Default 1813. Restart required."
              rules={[{ required: true, message: 'Required' }]}
            >
              <InputNumber min={1} max={65535} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="coa_port"
              label="CoA Port"
              extra="UDP port for Change of Authorization. Default 3799. Restart required for the coa_service container."
              rules={[{ required: true, message: 'Required' }]}
            >
              <InputNumber min={1} max={65535} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="default_eap_type"
              label="Default EAP Type"
              extra="Inner EAP method used when client doesn't specify one."
              rules={[{ required: true, message: 'Required' }]}
            >
              <Select>
                <Select.Option value="peap">PEAP (Windows-friendly)</Select.Option>
                <Select.Option value="ttls">EAP-TTLS (LDAP-friendly)</Select.Option>
                <Select.Option value="tls">EAP-TLS (cert-based)</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item
              name="tls_min_version"
              label="TLS Min Version"
              extra="Minimum TLS version accepted for EAP-TLS / PEAP / TTLS handshake."
              rules={[{ required: true, message: 'Required' }]}
            >
              <Select>
                <Select.Option value="1.0">1.0 (insecure, legacy only)</Select.Option>
                <Select.Option value="1.1">1.1 (deprecated)</Select.Option>
                <Select.Option value="1.2">1.2 (recommended)</Select.Option>
                <Select.Option value="1.3">1.3</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item
              name="tls_max_version"
              label="TLS Max Version"
              extra="Maximum TLS version offered to clients. Default 1.2 — see warning below if you select 1.3."
              rules={[{ required: true, message: 'Required' }]}
            >
              <Select>
                <Select.Option value="1.0">1.0</Select.Option>
                <Select.Option value="1.1">1.1</Select.Option>
                <Select.Option value="1.2">1.2 (recommended for 802.1X)</Select.Option>
                <Select.Option value="1.3">1.3 (limited supplicant support)</Select.Option>
              </Select>
            </Form.Item>
            {tlsMaxWatched === '1.3' && (
              <div
                style={{
                  marginTop: -8,
                  marginBottom: 16,
                  padding: '8px 12px',
                  background: '#fffbe6',
                  border: '1px solid #ffe58f',
                  borderRadius: 4,
                  color: '#664500',
                  fontSize: 12,
                }}
              >
                ⚠ <strong>TLS 1.3 has limited 802.1X supplicant support.</strong>{' '}
                Most Android, iOS, and older Windows clients will fail to connect with EAP-PEAP / EAP-TTLS / EAP-TLS over TLS 1.3.
                FreeRADIUS itself prints this warning at startup. Only enable TLS 1.3 max if all your client devices are
                verified to support EAP over TLS 1.3 (special wpa_supplicant builds or Windows 11 22H2+).
                Reference:{' '}
                <a href="https://wiki.freeradius.org/" target="_blank" rel="noopener noreferrer">
                  FreeRADIUS wiki
                </a>.
              </div>
            )}
            <Form.Item>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                onClick={handleSaveRadius}
                loading={radiusSaving}
              >
                Save Settings
              </Button>
            </Form.Item>
          </Form>
          <div style={{ marginTop: 12, color: '#999', fontSize: 12 }}>
            Port changes require a freeradius / coa_service container restart to take effect.
            EAP type and TLS version changes apply automatically when the watcher regenerates
            the freeradius config (~30s) or on the next freeradius restart.
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
