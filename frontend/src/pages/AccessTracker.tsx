/**
 * OpenRadiusWeb Access Tracker - 802.1X Authentication Log Viewer
 *
 * ClearPass-like interface for viewing and troubleshooting RADIUS authentication events.
 * Features:
 * - Real-time authentication feed
 * - Filtering by MAC, username, NAS, result, auth method
 * - Detailed failure reason with AD error codes
 * - Troubleshooting guidance per failure type
 * - Statistics dashboard (success rate, top failures, trends)
 * - Export to CSV/JSON
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Table, Card, Row, Col, Statistic, Tag, Input, Select, DatePicker,
  Button, Space, Modal, Descriptions, Timeline, Typography, Badge,
  Tabs, Alert, Tooltip, Progress, Collapse,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined,
  ExclamationCircleOutlined, SearchOutlined, ReloadOutlined,
  ExportOutlined, InfoCircleOutlined, QuestionCircleOutlined,
  WarningOutlined, BugOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import api from '../api';

const { Text, Title, Paragraph } = Typography;
const { RangePicker } = DatePicker;
const { Panel } = Collapse;

// ============================================================
// Types
// ============================================================
interface AuthLogEntry {
  id: string;
  timestamp: string;
  session_id: string;
  auth_result: 'success' | 'reject' | 'timeout' | 'error' | 'challenge';
  auth_method: string;
  eap_type: string;
  failure_reason: string | null;
  ad_error_code: string | null;
  ad_error_message: string | null;
  radius_reply_message: string | null;
  calling_station_id: string;
  username: string;
  user_domain: string | null;
  nas_ip: string;
  nas_port: number;
  nas_port_id: string;
  nas_identifier: string;
  assigned_vlan: number | null;
  assigned_vlan_name: string | null;
  filter_id: string | null;
  client_cert_cn: string | null;
  processing_time_ms: number;
  policy_matched: string | null;
  request_attributes?: Record<string, string>;
  response_attributes?: Record<string, string>;
}

interface TroubleshootingInfo {
  category: string;
  description: string;
  possible_causes: string[];
  remediation_steps: string[];
  severity: string;
  kb_url: string | null;
}

interface AuthStats {
  total_attempts: number;
  success_count: number;
  failure_count: number;
  success_rate: number;
  by_result: Record<string, number>;
  top_failure_reasons: Array<{ failure_reason: string; count: number }>;
  top_failing_users: Array<{ username: string; failure_count: number; reasons: string[] }>;
  top_failing_macs: Array<{ calling_station_id: string; failure_count: number; reasons: string[] }>;
}

// ============================================================
// Auth Result Tag Component
// ============================================================
const AuthResultTag: React.FC<{ result: string }> = ({ result }) => {
  const config: Record<string, { color: string; icon: React.ReactNode }> = {
    success: { color: 'success', icon: <CheckCircleOutlined /> },
    reject: { color: 'error', icon: <CloseCircleOutlined /> },
    timeout: { color: 'warning', icon: <ClockCircleOutlined /> },
    error: { color: 'error', icon: <ExclamationCircleOutlined /> },
    challenge: { color: 'processing', icon: <ClockCircleOutlined /> },
  };
  const { color, icon } = config[result] || { color: 'default', icon: null };
  return <Tag color={color} icon={icon}>{result.toUpperCase()}</Tag>;
};

// ============================================================
// Failure Category Tag
// ============================================================
const FailureCategoryTag: React.FC<{ category: string }> = ({ category }) => {
  const colors: Record<string, string> = {
    credential: 'orange',
    certificate: 'red',
    policy: 'blue',
    network: 'purple',
    system: 'volcano',
  };
  return <Tag color={colors[category] || 'default'}>{category}</Tag>;
};

// ============================================================
// Main Access Tracker Component
// ============================================================
const AccessTracker: React.FC = () => {
  const [logs, setLogs] = useState<AuthLogEntry[]>([]);
  const [stats, setStats] = useState<AuthStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selectedLog, setSelectedLog] = useState<AuthLogEntry | null>(null);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [troubleshooting, setTroubleshooting] = useState<TroubleshootingInfo | null>(null);
  const [relatedHistory, setRelatedHistory] = useState<AuthLogEntry[]>([]);

  // Filters
  const [filters, setFilters] = useState({
    auth_result: undefined as string | undefined,
    calling_station_id: '',
    username: '',
    nas_ip: '',
    auth_method: undefined as string | undefined,
    failure_reason: '',
    search: '',
    last_hours: 24,
  });

  // Auto-refresh
  const [autoRefresh, setAutoRefresh] = useState(false);

  // ============================================================
  // API Calls
  // ============================================================
  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('page_size', String(pageSize));
      params.set('last_hours', String(filters.last_hours));

      if (filters.auth_result) params.set('auth_result', filters.auth_result);
      if (filters.calling_station_id) params.set('calling_station_id', filters.calling_station_id);
      if (filters.username) params.set('username', filters.username);
      if (filters.nas_ip) params.set('nas_ip', filters.nas_ip);
      if (filters.auth_method) params.set('auth_method', filters.auth_method);
      if (filters.failure_reason) params.set('failure_reason', filters.failure_reason);
      if (filters.search) params.set('search', filters.search);

      const response = await api.get(`/radius/auth-log?${params}`);
      const data = response.data;
      setLogs(data.items || []);
      setTotal(data.total || 0);
    } catch (error) {
      console.error('Failed to fetch auth logs:', error);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, filters]);

  const fetchStats = useCallback(async () => {
    try {
      const response = await api.get(
        `/radius/auth-log/stats/summary?last_hours=${filters.last_hours}`
      );
      setStats(response.data);
    } catch (error) {
      console.error('Failed to fetch auth stats:', error);
    }
  }, [filters.last_hours]);

  const fetchLogDetail = async (logId: string) => {
    try {
      const response = await api.get(`/radius/auth-log/detail/${logId}`);
      const data = response.data;
      setSelectedLog(data.entry);
      setTroubleshooting(data.troubleshooting);
      setRelatedHistory(data.related_history || []);
      setDetailModalVisible(true);
    } catch (error) {
      console.error('Failed to fetch log detail:', error);
    }
  };

  useEffect(() => {
    fetchLogs();
    fetchStats();
  }, [fetchLogs, fetchStats]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchLogs();
      fetchStats();
    }, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchLogs, fetchStats]);

  // ============================================================
  // Table Columns
  // ============================================================
  const columns: ColumnsType<AuthLogEntry> = [
    {
      title: '時間',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 180,
      render: (ts: string) => dayjs(ts).format('YYYY-MM-DD HH:mm:ss'),
      sorter: true,
    },
    {
      title: '結果',
      dataIndex: 'auth_result',
      key: 'auth_result',
      width: 100,
      render: (result: string) => <AuthResultTag result={result} />,
      filters: [
        { text: 'Success', value: 'success' },
        { text: 'Reject', value: 'reject' },
        { text: 'Timeout', value: 'timeout' },
        { text: 'Error', value: 'error' },
      ],
    },
    {
      title: '使用者',
      dataIndex: 'username',
      key: 'username',
      width: 200,
      ellipsis: true,
      render: (username: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong>{username || '-'}</Text>
          {record.user_domain && (
            <Text type="secondary" style={{ fontSize: 12 }}>{record.user_domain}</Text>
          )}
        </Space>
      ),
    },
    {
      title: 'MAC 位址',
      dataIndex: 'calling_station_id',
      key: 'calling_station_id',
      width: 160,
      render: (mac: string) => <Text code>{mac || '-'}</Text>,
    },
    {
      title: '認證方式',
      dataIndex: 'auth_method',
      key: 'auth_method',
      width: 140,
      render: (method: string) => <Tag>{method || '-'}</Tag>,
    },
    {
      title: '交換器 (NAS)',
      key: 'nas',
      width: 200,
      render: (_: any, record: AuthLogEntry) => (
        <Space direction="vertical" size={0}>
          <Text>{record.nas_identifier || record.nas_ip}</Text>
          {record.nas_port_id && (
            <Text type="secondary" style={{ fontSize: 12 }}>{record.nas_port_id}</Text>
          )}
        </Space>
      ),
    },
    {
      title: 'VLAN',
      dataIndex: 'assigned_vlan',
      key: 'assigned_vlan',
      width: 80,
      render: (vlan: number | null, record) =>
        vlan ? (
          <Tooltip title={record.assigned_vlan_name}>
            <Tag color="blue">{vlan}</Tag>
          </Tooltip>
        ) : '-',
    },
    {
      title: '失敗原因',
      dataIndex: 'failure_reason',
      key: 'failure_reason',
      width: 300,
      ellipsis: true,
      render: (reason: string | null, record) => {
        if (!reason) return '-';
        return (
          <Space>
            <WarningOutlined style={{ color: '#ff4d4f' }} />
            <Tooltip title={record.ad_error_message || reason}>
              <Text type="danger" ellipsis style={{ maxWidth: 250 }}>
                {reason}
              </Text>
            </Tooltip>
            {record.ad_error_code && (
              <Tag color="red" style={{ fontSize: 10 }}>
                {record.ad_error_code}
              </Tag>
            )}
          </Space>
        );
      },
    },
    {
      title: '',
      key: 'actions',
      width: 50,
      render: (_: any, record: AuthLogEntry) => (
        <Button
          type="link"
          icon={<InfoCircleOutlined />}
          onClick={() => fetchLogDetail(record.id)}
          title="查看詳細資訊"
        />
      ),
    },
  ];

  // ============================================================
  // Render
  // ============================================================
  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>
        <Badge status={autoRefresh ? 'processing' : 'default'} />
        Access Tracker - 802.1X 認證日誌
      </Title>

      {/* Statistics Cards */}
      {stats && (
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col span={4}>
            <Card>
              <Statistic title="總認證次數" value={stats.total_attempts} />
            </Card>
          </Col>
          <Col span={4}>
            <Card>
              <Statistic
                title="成功"
                value={stats.success_count}
                valueStyle={{ color: '#3f8600' }}
                prefix={<CheckCircleOutlined />}
              />
            </Card>
          </Col>
          <Col span={4}>
            <Card>
              <Statistic
                title="失敗"
                value={stats.failure_count}
                valueStyle={{ color: '#cf1322' }}
                prefix={<CloseCircleOutlined />}
              />
            </Card>
          </Col>
          <Col span={4}>
            <Card>
              <Statistic
                title="成功率"
                value={stats.success_rate}
                suffix="%"
                precision={1}
              />
              <Progress
                percent={stats.success_rate}
                showInfo={false}
                strokeColor={stats.success_rate > 90 ? '#52c41a' : '#faad14'}
                size="small"
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card title="Top 失敗原因" size="small" bodyStyle={{ padding: '8px 16px' }}>
              {stats.top_failure_reasons.slice(0, 3).map((f, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <Text ellipsis style={{ maxWidth: 250, fontSize: 12 }}>
                    {f.failure_reason}
                  </Text>
                  <Badge count={f.count} style={{ backgroundColor: '#ff4d4f' }} />
                </div>
              ))}
            </Card>
          </Col>
        </Row>
      )}

      {/* Filters */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="搜尋 (使用者/MAC/NAS)"
            prefix={<SearchOutlined />}
            value={filters.search}
            onChange={e => setFilters(f => ({ ...f, search: e.target.value }))}
            onPressEnter={() => fetchLogs()}
            style={{ width: 220 }}
            allowClear
          />
          <Select
            placeholder="認證結果"
            value={filters.auth_result}
            onChange={v => setFilters(f => ({ ...f, auth_result: v }))}
            style={{ width: 120 }}
            allowClear
          >
            <Select.Option value="success">成功</Select.Option>
            <Select.Option value="reject">拒絕</Select.Option>
            <Select.Option value="timeout">逾時</Select.Option>
            <Select.Option value="error">錯誤</Select.Option>
          </Select>
          <Input
            placeholder="MAC 位址"
            value={filters.calling_station_id}
            onChange={e => setFilters(f => ({ ...f, calling_station_id: e.target.value }))}
            style={{ width: 160 }}
            allowClear
          />
          <Input
            placeholder="使用者名稱"
            value={filters.username}
            onChange={e => setFilters(f => ({ ...f, username: e.target.value }))}
            style={{ width: 150 }}
            allowClear
          />
          <Input
            placeholder="交換器 IP"
            value={filters.nas_ip}
            onChange={e => setFilters(f => ({ ...f, nas_ip: e.target.value }))}
            style={{ width: 140 }}
            allowClear
          />
          <Select
            placeholder="認證方式"
            value={filters.auth_method}
            onChange={v => setFilters(f => ({ ...f, auth_method: v }))}
            style={{ width: 140 }}
            allowClear
          >
            <Select.Option value="EAP-TLS">EAP-TLS</Select.Option>
            <Select.Option value="PEAP">PEAP</Select.Option>
            <Select.Option value="EAP-TTLS">EAP-TTLS</Select.Option>
            <Select.Option value="MAB">MAB</Select.Option>
          </Select>
          <Select
            value={filters.last_hours}
            onChange={v => setFilters(f => ({ ...f, last_hours: v }))}
            style={{ width: 120 }}
          >
            <Select.Option value={1}>最近 1 小時</Select.Option>
            <Select.Option value={4}>最近 4 小時</Select.Option>
            <Select.Option value={24}>最近 24 小時</Select.Option>
            <Select.Option value={72}>最近 3 天</Select.Option>
            <Select.Option value={168}>最近 7 天</Select.Option>
            <Select.Option value={720}>最近 30 天</Select.Option>
          </Select>
          <Button type="primary" icon={<SearchOutlined />} onClick={() => { setPage(1); fetchLogs(); fetchStats(); }}>
            查詢
          </Button>
          <Button
            icon={<ReloadOutlined spin={autoRefresh} />}
            onClick={() => setAutoRefresh(!autoRefresh)}
            type={autoRefresh ? 'primary' : 'default'}
          >
            {autoRefresh ? '停止自動更新' : '自動更新'}
          </Button>
          <Button icon={<ExportOutlined />}>匯出</Button>
        </Space>
      </Card>

      {/* Auth Log Table */}
      <Table
        columns={columns}
        dataSource={logs}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          pageSize: pageSize,
          total: total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 筆`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps || 50); },
        }}
        onRow={(record) => ({
          onClick: () => fetchLogDetail(record.id),
          style: { cursor: 'pointer' },
        })}
        rowClassName={(record) =>
          record.auth_result !== 'success' ? 'auth-failure-row' : ''
        }
        size="small"
        scroll={{ x: 1500 }}
      />

      {/* Detail Modal */}
      <Modal
        title={
          <Space>
            <span>認證詳細資訊</span>
            {selectedLog && <AuthResultTag result={selectedLog.auth_result} />}
          </Space>
        }
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={null}
        width={900}
      >
        {selectedLog && (
          <Tabs
            items={[
              {
                key: 'overview',
                label: '概覽',
                children: (
                  <>
                    <Descriptions bordered column={2} size="small">
                      <Descriptions.Item label="時間">{dayjs(selectedLog.timestamp).format('YYYY-MM-DD HH:mm:ss')}</Descriptions.Item>
                      <Descriptions.Item label="結果"><AuthResultTag result={selectedLog.auth_result} /></Descriptions.Item>
                      <Descriptions.Item label="使用者">{selectedLog.username || '-'}</Descriptions.Item>
                      <Descriptions.Item label="網域">{selectedLog.user_domain || '-'}</Descriptions.Item>
                      <Descriptions.Item label="MAC 位址"><Text code>{selectedLog.calling_station_id}</Text></Descriptions.Item>
                      <Descriptions.Item label="認證方式"><Tag>{selectedLog.auth_method}</Tag></Descriptions.Item>
                      <Descriptions.Item label="交換器">{selectedLog.nas_identifier || selectedLog.nas_ip}</Descriptions.Item>
                      <Descriptions.Item label="端口">{selectedLog.nas_port_id || selectedLog.nas_port}</Descriptions.Item>
                      <Descriptions.Item label="指派 VLAN">{selectedLog.assigned_vlan || '-'}</Descriptions.Item>
                      <Descriptions.Item label="套用策略">{selectedLog.policy_matched || '-'}</Descriptions.Item>
                      <Descriptions.Item label="處理時間">{selectedLog.processing_time_ms} ms</Descriptions.Item>
                      <Descriptions.Item label="Session ID">{selectedLog.session_id || '-'}</Descriptions.Item>
                    </Descriptions>

                    {/* Certificate Info */}
                    {selectedLog.client_cert_cn && (
                      <>
                        <Title level={5} style={{ marginTop: 16 }}>憑證資訊</Title>
                        <Descriptions bordered column={2} size="small">
                          <Descriptions.Item label="Client CN">{selectedLog.client_cert_cn}</Descriptions.Item>
                          <Descriptions.Item label="Issuer">{selectedLog.client_cert_cn}</Descriptions.Item>
                        </Descriptions>
                      </>
                    )}
                  </>
                ),
              },
              {
                key: 'failure',
                label: (
                  <Space>
                    <BugOutlined />
                    失敗分析
                    {selectedLog.auth_result !== 'success' && <Badge dot />}
                  </Space>
                ),
                children: selectedLog.auth_result !== 'success' ? (
                  <>
                    {/* Failure Reason */}
                    <Alert
                      type="error"
                      showIcon
                      message={selectedLog.failure_reason || '未知原因'}
                      description={
                        <Space direction="vertical">
                          {selectedLog.ad_error_code && (
                            <Text>AD 錯誤代碼: <Tag color="red">{selectedLog.ad_error_code}</Tag></Text>
                          )}
                          {selectedLog.ad_error_message && (
                            <Text type="secondary">AD 詳細訊息: {selectedLog.ad_error_message}</Text>
                          )}
                          {selectedLog.radius_reply_message && (
                            <Text type="secondary">RADIUS Reply: {selectedLog.radius_reply_message}</Text>
                          )}
                        </Space>
                      }
                      style={{ marginBottom: 16 }}
                    />

                    {/* Troubleshooting Guidance */}
                    {troubleshooting && (
                      <Card
                        title={
                          <Space>
                            <QuestionCircleOutlined />
                            排錯指南
                            <FailureCategoryTag category={troubleshooting.category} />
                          </Space>
                        }
                        size="small"
                      >
                        <Paragraph>{troubleshooting.description}</Paragraph>

                        <Collapse ghost>
                          <Panel header="可能原因" key="causes">
                            <ul>
                              {troubleshooting.possible_causes.map((cause, i) => (
                                <li key={i}>{cause}</li>
                              ))}
                            </ul>
                          </Panel>
                          <Panel header="修復步驟" key="remediation">
                            <ol>
                              {troubleshooting.remediation_steps.map((step, i) => (
                                <li key={i}><Text code>{step}</Text></li>
                              ))}
                            </ol>
                          </Panel>
                        </Collapse>

                        {troubleshooting.kb_url && (
                          <Button type="link" href={troubleshooting.kb_url} target="_blank">
                            查看知識庫文章
                          </Button>
                        )}
                      </Card>
                    )}
                  </>
                ) : (
                  <Alert type="success" showIcon message="認證成功，無失敗資訊" />
                ),
              },
              {
                key: 'history',
                label: '相關歷史',
                children: (
                  <Timeline
                    items={relatedHistory.map(entry => ({
                      color: entry.auth_result === 'success' ? 'green' : 'red',
                      children: (
                        <Space direction="vertical" size={0}>
                          <Space>
                            <Text type="secondary">{dayjs(entry.timestamp).format('MM-DD HH:mm:ss')}</Text>
                            <AuthResultTag result={entry.auth_result} />
                            <Tag>{entry.auth_method}</Tag>
                          </Space>
                          {entry.failure_reason && (
                            <Text type="danger" style={{ fontSize: 12 }}>{entry.failure_reason}</Text>
                          )}
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            NAS: {entry.nas_ip} | Port: {entry.nas_port_id}
                          </Text>
                        </Space>
                      ),
                    }))}
                  />
                ),
              },
              {
                key: 'raw',
                label: 'RADIUS 屬性',
                children: (
                  <Row gutter={16}>
                    <Col span={12}>
                      <Title level={5}>Request Attributes</Title>
                      <pre style={{ fontSize: 11, maxHeight: 400, overflow: 'auto', background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
                        {JSON.stringify(selectedLog.request_attributes, null, 2)}
                      </pre>
                    </Col>
                    <Col span={12}>
                      <Title level={5}>Response Attributes</Title>
                      <pre style={{ fontSize: 11, maxHeight: 400, overflow: 'auto', background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
                        {JSON.stringify(selectedLog.response_attributes, null, 2)}
                      </pre>
                    </Col>
                  </Row>
                ),
              },
            ]}
          />
        )}
      </Modal>

      <style>{`
        .auth-failure-row {
          background-color: #fff2f0 !important;
        }
        .auth-failure-row:hover > td {
          background-color: #ffeded !important;
        }
      `}</style>
    </div>
  );
};

export default AccessTracker;
