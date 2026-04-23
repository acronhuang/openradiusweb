import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Row, Col, Card, Statistic, Table, Tag, Typography, Progress, Space, Tooltip,
  Modal, Descriptions, Tabs, Alert, Timeline, Collapse, Badge, message,
} from 'antd';
import {
  LaptopOutlined, CheckCircleOutlined, WarningOutlined,
  CloseCircleOutlined, ApartmentOutlined, SafetyOutlined,
  ArrowRightOutlined, ClockCircleOutlined, InfoCircleOutlined,
  QuestionCircleOutlined, BugOutlined, ExclamationCircleOutlined,
} from '@ant-design/icons';
import api from '../api';

const { Title, Text, Paragraph } = Typography;
const { Panel } = Collapse;

const clickableCard: React.CSSProperties = {
  cursor: 'pointer',
  transition: 'all 0.3s',
  borderRadius: 8,
};

interface AuthLogEntry {
  id: string;
  timestamp: string;
  session_id: string;
  auth_result: string;
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

const resultConfig: Record<string, { color: string; icon: React.ReactNode }> = {
  success: { color: 'green', icon: <CheckCircleOutlined /> },
  reject: { color: 'red', icon: <CloseCircleOutlined /> },
  timeout: { color: 'orange', icon: <ClockCircleOutlined /> },
  error: { color: 'red', icon: <ExclamationCircleOutlined /> },
  challenge: { color: 'processing', icon: <ClockCircleOutlined /> },
  Accept: { color: 'green', icon: <CheckCircleOutlined /> },
  Reject: { color: 'red', icon: <CloseCircleOutlined /> },
};

const AuthResultTag: React.FC<{ result: string }> = ({ result }) => {
  const cfg = resultConfig[result] || { color: 'default', icon: null };
  return <Tag color={cfg.color} icon={cfg.icon}>{result}</Tag>;
};

export default function Dashboard() {
  const navigate = useNavigate();

  const [stats, setStats] = useState({
    totalDevices: 0, onlineDevices: 0, switches: 0, policies: 0,
    authSuccess: 0, authFailed: 0, authRate: 0,
  });
  const [recentAuth, setRecentAuth] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  // Detail modal state
  const [detailVisible, setDetailVisible] = useState(false);
  const [selectedLog, setSelectedLog] = useState<AuthLogEntry | null>(null);
  const [troubleshooting, setTroubleshooting] = useState<TroubleshootingInfo | null>(null);
  const [relatedHistory, setRelatedHistory] = useState<AuthLogEntry[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [devRes, onlineRes, swRes, polRes, authRes, authStatsRes] = await Promise.allSettled([
        api.get('/devices', { params: { page_size: 1 } }),
        api.get('/devices', { params: { page_size: 1, status: 'online' } }),
        api.get('/network-devices', { params: { page_size: 1 } }),
        api.get('/policies', { params: { page_size: 1 } }),
        api.get('/radius/auth-log', { params: { page_size: 10 } }),
        api.get('/radius/auth-log/stats/summary', { params: { last_hours: 24 } }),
      ]);

      const s = { ...stats };
      if (devRes.status === 'fulfilled') s.totalDevices = devRes.value.data.total || 0;
      if (onlineRes.status === 'fulfilled') s.onlineDevices = onlineRes.value.data.total || 0;
      if (swRes.status === 'fulfilled') s.switches = swRes.value.data.total || 0;
      if (polRes.status === 'fulfilled') s.policies = polRes.value.data.total || 0;
      if (authStatsRes.status === 'fulfilled') {
        const d = authStatsRes.value.data;
        s.authSuccess = d.success_count || 0;
        s.authFailed = d.failure_count || 0;
        s.authRate = d.success_rate ?? 100;
      }
      setStats(s);
      if (authRes.status === 'fulfilled') setRecentAuth(authRes.value.data.items || []);
    } catch { message.error('Failed to load dashboard data'); }
    setLoading(false);
  };

  const openDetail = async (logId: string) => {
    setDetailLoading(true);
    setDetailVisible(true);
    try {
      const res = await api.get(`/radius/auth-log/detail/${logId}`);
      const data = res.data;
      setSelectedLog(data.entry);
      setTroubleshooting(data.troubleshooting || null);
      setRelatedHistory(data.related_history || []);
    } catch (err) {
      console.error('Failed to fetch detail:', err);
    }
    setDetailLoading(false);
  };

  const closeDetail = () => {
    setDetailVisible(false);
    setSelectedLog(null);
    setTroubleshooting(null);
    setRelatedHistory([]);
  };

  const fmtTime = (v: string) => v ? new Date(v).toLocaleString() : '-';

  return (
    <div>
      <Title level={4}>Dashboard</Title>

      {/* ===== Summary Cards ===== */}
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card hoverable style={clickableCard} onClick={() => navigate('/devices')}>
            <Statistic
              title={<Space>Total Devices <ArrowRightOutlined style={{ fontSize: 12, color: '#999' }} /></Space>}
              value={stats.totalDevices} prefix={<LaptopOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable style={clickableCard} onClick={() => navigate('/devices')}>
            <Statistic
              title={<Space>Online <ArrowRightOutlined style={{ fontSize: 12, color: '#999' }} /></Space>}
              value={stats.onlineDevices} prefix={<CheckCircleOutlined style={{ color: '#52c41a' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable style={clickableCard} onClick={() => navigate('/switches')}>
            <Statistic
              title={<Space>Switches <ArrowRightOutlined style={{ fontSize: 12, color: '#999' }} /></Space>}
              value={stats.switches} prefix={<ApartmentOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable style={clickableCard} onClick={() => navigate('/policies')}>
            <Statistic
              title={<Space>Active Policies <ArrowRightOutlined style={{ fontSize: 12, color: '#999' }} /></Space>}
              value={stats.policies} prefix={<SafetyOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* ===== Auth Rate + Recent Events ===== */}
      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={8}>
          <Card title="Authentication Success Rate" hoverable style={clickableCard}
            onClick={() => navigate('/access-tracker')}
            extra={<ArrowRightOutlined style={{ color: '#999' }} />}>
            <Progress type="dashboard" percent={stats.authRate}
              strokeColor={stats.authRate > 90 ? '#52c41a' : stats.authRate > 70 ? '#faad14' : '#ff4d4f'} />
            <div style={{ textAlign: 'center', marginTop: 8 }}>
              <Space size="large">
                <Statistic title="Success" value={stats.authSuccess}
                  valueStyle={{ color: '#3f8600', fontSize: 16 }} prefix={<CheckCircleOutlined />} />
                <Statistic title="Failed" value={stats.authFailed}
                  valueStyle={{ color: '#cf1322', fontSize: 16 }} prefix={<CloseCircleOutlined />} />
              </Space>
            </div>
          </Card>
        </Col>
        <Col span={16}>
          <Card title="Recent Authentication Events"
            extra={<a onClick={() => navigate('/access-tracker')}>View All <ArrowRightOutlined /></a>}>
            <Table dataSource={recentAuth} size="small" pagination={false}
              loading={loading} rowKey="id"
              onRow={(record) => ({
                onClick: () => openDetail(record.id),
                style: {
                  cursor: 'pointer',
                  backgroundColor: record.auth_result !== 'success' && record.auth_result !== 'Accept'
                    ? '#fff2f0' : undefined,
                },
              })}
              columns={[
                { title: 'Time', dataIndex: 'timestamp', width: 160,
                  render: (v: string) => fmtTime(v) },
                { title: 'Username', dataIndex: 'username',
                  render: (v: string) => v || <Text type="secondary">-</Text> },
                { title: 'MAC', dataIndex: 'calling_station_id',
                  render: (v: string) => <Text code>{v || '-'}</Text> },
                { title: 'NAS', dataIndex: 'nas_identifier', width: 120,
                  render: (v: string, r: any) => v || r.nas_ip || '-' },
                { title: 'Result', dataIndex: 'auth_result', width: 100,
                  render: (v: string) => <AuthResultTag result={v} /> },
                { title: '', key: 'action', width: 40,
                  render: (_: any, r: any) => (
                    <InfoCircleOutlined style={{ color: '#1677ff' }}
                      onClick={(e) => { e.stopPropagation(); openDetail(r.id); }} />
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      {/* ===== Auth Detail Modal ===== */}
      <Modal
        title={
          <Space>
            <span>Authentication Detail</span>
            {selectedLog && <AuthResultTag result={selectedLog.auth_result} />}
          </Space>
        }
        open={detailVisible}
        onCancel={closeDetail}
        footer={null}
        width={900}
        loading={detailLoading}
      >
        {selectedLog && (
          <Tabs items={[
            {
              key: 'overview',
              label: 'Overview',
              children: (
                <>
                  <Descriptions bordered column={2} size="small">
                    <Descriptions.Item label="Timestamp">{fmtTime(selectedLog.timestamp)}</Descriptions.Item>
                    <Descriptions.Item label="Result"><AuthResultTag result={selectedLog.auth_result} /></Descriptions.Item>
                    <Descriptions.Item label="Username">{selectedLog.username || '-'}</Descriptions.Item>
                    <Descriptions.Item label="Domain">{selectedLog.user_domain || '-'}</Descriptions.Item>
                    <Descriptions.Item label="MAC Address"><Text code>{selectedLog.calling_station_id}</Text></Descriptions.Item>
                    <Descriptions.Item label="Auth Method"><Tag>{selectedLog.auth_method}</Tag></Descriptions.Item>
                    <Descriptions.Item label="EAP Type">{selectedLog.eap_type || '-'}</Descriptions.Item>
                    <Descriptions.Item label="Processing Time">{selectedLog.processing_time_ms ?? '-'} ms</Descriptions.Item>
                    <Descriptions.Item label="NAS (Switch)">{selectedLog.nas_identifier || selectedLog.nas_ip || '-'}</Descriptions.Item>
                    <Descriptions.Item label="NAS Port">{selectedLog.nas_port_id || selectedLog.nas_port || '-'}</Descriptions.Item>
                    <Descriptions.Item label="Assigned VLAN">
                      {selectedLog.assigned_vlan
                        ? <Tag color="blue">{selectedLog.assigned_vlan} {selectedLog.assigned_vlan_name ? `(${selectedLog.assigned_vlan_name})` : ''}</Tag>
                        : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="Policy Matched">{selectedLog.policy_matched || '-'}</Descriptions.Item>
                    <Descriptions.Item label="Session ID" span={2}>
                      <Text copyable style={{ fontSize: 12 }}>{selectedLog.session_id || '-'}</Text>
                    </Descriptions.Item>
                  </Descriptions>

                  {selectedLog.client_cert_cn && (
                    <>
                      <Title level={5} style={{ marginTop: 16 }}>Certificate Info</Title>
                      <Descriptions bordered column={2} size="small">
                        <Descriptions.Item label="Client CN">{selectedLog.client_cert_cn}</Descriptions.Item>
                      </Descriptions>
                    </>
                  )}

                  {selectedLog.failure_reason && (
                    <Alert
                      type="error" showIcon
                      message={selectedLog.failure_reason}
                      description={
                        <Space direction="vertical">
                          {selectedLog.ad_error_code && (
                            <Text>AD Error Code: <Tag color="red">{selectedLog.ad_error_code}</Tag></Text>
                          )}
                          {selectedLog.ad_error_message && (
                            <Text type="secondary">{selectedLog.ad_error_message}</Text>
                          )}
                          {selectedLog.radius_reply_message && (
                            <Text type="secondary">RADIUS Reply: {selectedLog.radius_reply_message}</Text>
                          )}
                        </Space>
                      }
                      style={{ marginTop: 16 }}
                    />
                  )}
                </>
              ),
            },
            {
              key: 'troubleshoot',
              label: (
                <Space>
                  <BugOutlined />
                  Failure Analysis
                  {selectedLog.auth_result !== 'success' && <Badge dot />}
                </Space>
              ),
              children: selectedLog.auth_result !== 'success' ? (
                <>
                  {selectedLog.failure_reason && (
                    <Alert type="error" showIcon style={{ marginBottom: 16 }}
                      message={selectedLog.failure_reason}
                      description={
                        <Space direction="vertical">
                          {selectedLog.ad_error_code && <Text>AD Error: <Tag color="red">{selectedLog.ad_error_code}</Tag></Text>}
                          {selectedLog.ad_error_message && <Text type="secondary">{selectedLog.ad_error_message}</Text>}
                          {selectedLog.radius_reply_message && <Text type="secondary">RADIUS: {selectedLog.radius_reply_message}</Text>}
                        </Space>
                      }
                    />
                  )}
                  {troubleshooting ? (
                    <Card title={
                      <Space>
                        <QuestionCircleOutlined />
                        Troubleshooting Guide
                        <Tag color={
                          troubleshooting.category === 'credential' ? 'orange' :
                          troubleshooting.category === 'certificate' ? 'red' :
                          troubleshooting.category === 'policy' ? 'blue' :
                          troubleshooting.category === 'network' ? 'purple' : 'volcano'
                        }>{troubleshooting.category}</Tag>
                      </Space>
                    } size="small">
                      <Paragraph>{troubleshooting.description}</Paragraph>
                      <Collapse ghost>
                        <Panel header="Possible Causes" key="causes">
                          <ul>{troubleshooting.possible_causes.map((c, i) => <li key={i}>{c}</li>)}</ul>
                        </Panel>
                        <Panel header="Remediation Steps" key="fix">
                          <ol>{troubleshooting.remediation_steps.map((s, i) => <li key={i}><Text code>{s}</Text></li>)}</ol>
                        </Panel>
                      </Collapse>
                    </Card>
                  ) : (
                    <Alert type="info" showIcon message="No troubleshooting guide available for this failure type." />
                  )}
                </>
              ) : (
                <Alert type="success" showIcon message="Authentication succeeded - no failure to analyze." />
              ),
            },
            {
              key: 'history',
              label: `Related History (${relatedHistory.length})`,
              children: relatedHistory.length > 0 ? (
                <Timeline items={relatedHistory.map(entry => ({
                  color: entry.auth_result === 'success' ? 'green' : 'red',
                  children: (
                    <Space direction="vertical" size={0}>
                      <Space>
                        <Text type="secondary">{fmtTime(entry.timestamp)}</Text>
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
                }))} />
              ) : (
                <Text type="secondary">No related authentication history for this MAC address.</Text>
              ),
            },
            {
              key: 'raw',
              label: 'RADIUS Attributes',
              children: (
                <Row gutter={16}>
                  <Col span={12}>
                    <Title level={5}>Request Attributes</Title>
                    <pre style={{ fontSize: 11, maxHeight: 400, overflow: 'auto', background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
                      {JSON.stringify(selectedLog.request_attributes, null, 2) || 'N/A'}
                    </pre>
                  </Col>
                  <Col span={12}>
                    <Title level={5}>Response Attributes</Title>
                    <pre style={{ fontSize: 11, maxHeight: 400, overflow: 'auto', background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
                      {JSON.stringify(selectedLog.response_attributes, null, 2) || 'N/A'}
                    </pre>
                  </Col>
                </Row>
              ),
            },
          ]} />
        )}
      </Modal>
    </div>
  );
}
