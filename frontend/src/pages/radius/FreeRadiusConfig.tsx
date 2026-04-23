import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Tabs, message, Popconfirm, Descriptions, Row, Col,
} from 'antd';
import {
  ReloadOutlined, EyeOutlined, ThunderboltOutlined,
  CheckCircleOutlined, ExclamationCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '../../api';

const { Title, Text } = Typography;

interface ConfigStatus {
  last_applied_at: string | null;
  status: 'active' | 'error' | 'never_applied';
  error_message: string | null;
  ldap_servers_count: number;
  realms_count: number;
  nas_clients_count: number;
  active_certs_count: number;
}

interface ConfigPreview {
  files: { name: string; content: string }[];
}

interface ConfigHistoryEntry {
  id: string;
  timestamp: string;
  user: string;
  action: string;
  details: string;
}

const STATUS_MAP: Record<string, { badge: 'success' | 'error' | 'default'; text: string }> = {
  active: { badge: 'success', text: 'Active' },
  error: { badge: 'error', text: 'Error' },
  never_applied: { badge: 'default', text: 'Never Applied' },
};

export default function FreeRadiusConfig() {
  const [configStatus, setConfigStatus] = useState<ConfigStatus | null>(null);
  const [history, setHistory] = useState<ConfigHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState<ConfigPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    loadStatus();
    loadHistory();
  }, []);

  const loadStatus = async () => {
    setLoading(true);
    try {
      // Fetch config status + source data counts in parallel
      const [configRes, previewRes] = await Promise.allSettled([
        api.get('/freeradius/config'),
        api.post('/freeradius/config/preview'),
      ]);

      const configs = configRes.status === 'fulfilled' ? (configRes.value.data.configs || []) : [];
      const sourceData = previewRes.status === 'fulfilled'
        ? (previewRes.value.data?.source_data || {})
        : {};

      const lastApplied = configs.reduce((latest: string | null, c: any) => {
        if (c.last_applied_at && (!latest || c.last_applied_at > latest)) return c.last_applied_at;
        return latest;
      }, null);
      const hasError = configs.some((c: any) => c.status === 'error');
      const errorMsg = configs.find((c: any) => c.error_message)?.error_message || null;

      setConfigStatus({
        last_applied_at: lastApplied,
        status: hasError ? 'error' : (configs.length === 0 || !lastApplied) ? 'never_applied' : 'active',
        error_message: errorMsg,
        ldap_servers_count: sourceData.ldap_servers ?? 0,
        realms_count: sourceData.realms ?? 0,
        nas_clients_count: sourceData.nas_clients ?? 0,
        active_certs_count: sourceData.active_certificates ?? 0,
      });
    } catch { message.error('Failed to load FreeRADIUS config status'); }
    setLoading(false);
  };

  const loadHistory = async () => {
    setHistoryLoading(true);
    try {
      const res = await api.get('/freeradius/config/history');
      const items = (res.data.items || []).map((item: any) => ({
        id: item.id,
        timestamp: item.timestamp,
        user: item.username || item.user_id || '-',
        action: item.action,
        details: typeof item.details === 'object' ? JSON.stringify(item.details) : (item.details || ''),
      }));
      setHistory(items);
    } catch { /* no history yet is OK */ }
    setHistoryLoading(false);
  };

  const handlePreview = async () => {
    setPreviewLoading(true);
    setPreviewOpen(true);
    try {
      const res = await api.post('/freeradius/config/preview');
      const configs = res.data.configs || [];
      if (configs.length > 0) {
        setPreviewData({
          files: configs.map((c: any) => ({
            name: `${c.config_type}/${c.config_name}`,
            content: c.config_content || '# No content generated yet',
          })),
        });
      } else {
        setPreviewData({
          files: [{ name: 'info', content: '# No configuration files generated yet.\n# Add LDAP servers, realms, NAS clients, or certificates first.' }],
        });
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Failed to generate preview');
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleApply = async () => {
    setApplying(true);
    try {
      await api.post('/freeradius/config/apply');
      message.success('FreeRADIUS configuration apply request sent');
      loadStatus();
      loadHistory();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Failed to apply configuration');
    } finally {
      setApplying(false);
    }
  };

  const statusInfo = configStatus
    ? STATUS_MAP[configStatus.status] || STATUS_MAP.never_applied
    : STATUS_MAP.never_applied;

  const historyColumns: ColumnsType<ConfigHistoryEntry> = [
    {
      title: 'Timestamp', dataIndex: 'timestamp', width: 180,
      render: (v) => v ? new Date(v).toLocaleString() : '-',
      defaultSortOrder: 'descend',
      sorter: (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    },
    { title: 'User', dataIndex: 'user', width: 140 },
    {
      title: 'Action', dataIndex: 'action', width: 140,
      render: (v) => {
        const color = v === 'apply' ? 'blue' : v === 'error' ? 'red' : 'default';
        return <Tag color={color}>{v}</Tag>;
      },
    },
    { title: 'Details', dataIndex: 'details', ellipsis: true },
  ];

  return (
    <div>
      <Title level={4}>FreeRADIUS Configuration</Title>

      {/* Status Card */}
      <Card style={{ marginBottom: 16 }} loading={loading}>
        <Row gutter={24}>
          <Col span={12}>
            <Descriptions column={1} size="small" title="Configuration Status">
              <Descriptions.Item label="Status">
                <Badge status={statusInfo.badge} text={statusInfo.text} />
              </Descriptions.Item>
              <Descriptions.Item label="Last Applied">
                {configStatus?.last_applied_at
                  ? new Date(configStatus.last_applied_at).toLocaleString()
                  : 'Never'}
              </Descriptions.Item>
              {configStatus?.error_message && (
                <Descriptions.Item label="Error">
                  <Text type="danger">{configStatus.error_message}</Text>
                </Descriptions.Item>
              )}
            </Descriptions>
          </Col>
          <Col span={12}>
            <Descriptions column={2} size="small" title="Source Data Summary">
              <Descriptions.Item label="LDAP Servers">
                <Badge
                  count={configStatus?.ldap_servers_count ?? 0}
                  showZero
                  style={{ backgroundColor: (configStatus?.ldap_servers_count ?? 0) > 0 ? '#1677ff' : '#d9d9d9' }}
                />
              </Descriptions.Item>
              <Descriptions.Item label="Realms">
                <Badge
                  count={configStatus?.realms_count ?? 0}
                  showZero
                  style={{ backgroundColor: (configStatus?.realms_count ?? 0) > 0 ? '#1677ff' : '#d9d9d9' }}
                />
              </Descriptions.Item>
              <Descriptions.Item label="NAS Clients">
                <Badge
                  count={configStatus?.nas_clients_count ?? 0}
                  showZero
                  style={{ backgroundColor: (configStatus?.nas_clients_count ?? 0) > 0 ? '#1677ff' : '#d9d9d9' }}
                />
              </Descriptions.Item>
              <Descriptions.Item label="Active Certs">
                <Badge
                  count={configStatus?.active_certs_count ?? 0}
                  showZero
                  style={{ backgroundColor: (configStatus?.active_certs_count ?? 0) > 0 ? '#52c41a' : '#d9d9d9' }}
                />
              </Descriptions.Item>
            </Descriptions>
          </Col>
        </Row>

        <Space style={{ marginTop: 16 }}>
          <Button icon={<EyeOutlined />} onClick={handlePreview} loading={previewLoading}>
            Preview Config
          </Button>
          <Popconfirm
            title="Apply FreeRADIUS Configuration?"
            description="This will regenerate and apply all FreeRADIUS config files and restart the service."
            icon={<ExclamationCircleOutlined style={{ color: '#faad14' }} />}
            onConfirm={handleApply}
          >
            <Button type="primary" icon={<ThunderboltOutlined />} loading={applying}>
              Apply Config
            </Button>
          </Popconfirm>
          <Button icon={<ReloadOutlined />} onClick={() => { loadStatus(); loadHistory(); }}>
            Refresh
          </Button>
        </Space>
      </Card>

      {/* Config History */}
      <Card title="Configuration History">
        <Table
          columns={historyColumns}
          dataSource={history}
          rowKey="id"
          loading={historyLoading}
          size="small"
          pagination={{ pageSize: 20, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      {/* Preview Modal */}
      <Modal
        title="FreeRADIUS Configuration Preview"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={[
          <Button key="close" onClick={() => setPreviewOpen(false)}>Close</Button>,
        ]}
        width="90%"
        style={{ top: 20 }}
        styles={{ body: { height: 'calc(100vh - 200px)', overflow: 'auto' } }}
        destroyOnClose
      >
        {previewLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>Loading configuration preview...</div>
        ) : previewData?.files && previewData.files.length > 0 ? (
          <Tabs
            items={previewData.files.map((file, idx) => ({
              key: String(idx),
              label: file.name,
              children: (
                <pre style={{
                  background: '#1e1e1e',
                  color: '#d4d4d4',
                  padding: 16,
                  borderRadius: 6,
                  overflow: 'auto',
                  maxHeight: 'calc(100vh - 340px)',
                  fontSize: 12,
                  lineHeight: 1.5,
                  fontFamily: "'Consolas', 'Monaco', 'Courier New', monospace",
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}>
                  {file.content}
                </pre>
              ),
            }))}
          />
        ) : (
          <Text type="secondary">No configuration files generated.</Text>
        )}
      </Modal>
    </div>
  );
}
