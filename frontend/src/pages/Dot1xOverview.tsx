import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Row, Col, Card, Statistic, Tag, Typography, Space, Table, Progress, Badge, Button, message,
} from 'antd';
import {
  SafetyCertificateOutlined, LaptopOutlined, ApartmentOutlined,
  KeyOutlined, GlobalOutlined, SafetyOutlined, CheckCircleOutlined,
  CloseCircleOutlined, WarningOutlined, ArrowRightOutlined,
  CloudServerOutlined, TeamOutlined,
} from '@ant-design/icons';
import api from '../api';

const { Title, Text } = Typography;

const clickableCard: React.CSSProperties = {
  cursor: 'pointer',
  transition: 'all 0.3s',
  borderRadius: 8,
};

const PURPOSE_COLORS: Record<string, string> = {
  corporate: 'blue',
  guest: 'green',
  quarantine: 'red',
  iot: 'orange',
  voip: 'purple',
  printer: 'cyan',
  remediation: 'gold',
  management: 'geekblue',
};

interface OverviewData {
  eap_methods: {
    enabled: string[];
    default: string;
    tls_min_version: string;
    auth_port: string;
    acct_port: string;
  };
  certificates: {
    ca_count: number;
    server_count: number;
    ca_active: boolean;
    server_active: boolean;
    nearest_expiry: string | null;
    nearest_expiry_name: string | null;
  };
  vlans: {
    total: number;
    by_purpose: Record<string, { vlan_id: number; name: string }[]>;
  };
  mab_devices: {
    total: number;
    enabled: number;
    expired: number;
  };
  realms: { total: number; local: number; proxy: number };
  nas_clients: { total: number; enabled: number };
  group_vlan_mappings: { total: number; enabled: number };
  policies: { total: number; enabled: number };
  auth_stats_24h: {
    total: number;
    success: number;
    failed: number;
    success_rate: number;
    by_method: Record<string, number>;
  };
}

export default function Dot1xOverview() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.get('/dot1x/overview');
      setData(res.data);
    } catch { message.error('Failed to load 802.1X overview'); }
    setLoading(false);
  };

  if (loading || !data) {
    return <Card loading={loading}><Title level={4}>802.1X Overview</Title></Card>;
  }

  const certStatus = data.certificates.ca_active && data.certificates.server_active;

  return (
    <div>
      <Title level={4}>802.1X Authentication Overview</Title>

      {/* Row 1: Summary Cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card style={clickableCard} hoverable onClick={() => navigate('/radius/realms')}>
            <Statistic
              title="EAP Methods"
              value={data.eap_methods.enabled.length}
              prefix={<SafetyOutlined />}
              suffix="enabled"
            />
            <div style={{ marginTop: 8 }}>
              {data.eap_methods.enabled.map(m => (
                <Tag key={m} color="blue" style={{ marginBottom: 4 }}>{m}</Tag>
              ))}
            </div>
            <Text type="secondary">Default: {data.eap_methods.default.toUpperCase()}</Text>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={clickableCard} hoverable onClick={() => navigate('/radius/certificates')}>
            <Statistic
              title="Certificates"
              value={data.certificates.ca_count + data.certificates.server_count}
              prefix={certStatus ? <CheckCircleOutlined style={{ color: '#52c41a' }} /> : <WarningOutlined style={{ color: '#faad14' }} />}
            />
            <div style={{ marginTop: 8 }}>
              <Space direction="vertical" size={2}>
                <span>
                  CA: {data.certificates.ca_active
                    ? <Badge status="success" text={`Active (${data.certificates.ca_count})`} />
                    : <Badge status="error" text="No active CA" />}
                </span>
                <span>
                  Server: {data.certificates.server_active
                    ? <Badge status="success" text={`Active (${data.certificates.server_count})`} />
                    : <Badge status="error" text="No active server cert" />}
                </span>
              </Space>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={clickableCard} hoverable onClick={() => navigate('/radius/mab-devices')}>
            <Statistic
              title="MAB Devices"
              value={data.mab_devices.enabled}
              prefix={<LaptopOutlined />}
              suffix={`/ ${data.mab_devices.total}`}
            />
            <div style={{ marginTop: 8 }}>
              {data.mab_devices.expired > 0 && (
                <Tag color="red">{data.mab_devices.expired} expired</Tag>
              )}
              <Text type="secondary">MAC bypass whitelist</Text>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={clickableCard} hoverable onClick={() => navigate('/radius/nas-clients')}>
            <Statistic
              title="NAS Clients"
              value={data.nas_clients.enabled}
              prefix={<CloudServerOutlined />}
              suffix={`/ ${data.nas_clients.total}`}
            />
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">
                {data.realms.total} realms | {data.policies.enabled} policies
              </Text>
            </div>
          </Card>
        </Col>
      </Row>

      {/* Row 2: VLAN Map + Auth Stats */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={12}>
          <Card
            title="VLAN Assignment Map"
            extra={<Button type="link" onClick={() => navigate('/radius/vlans')}>Manage <ArrowRightOutlined /></Button>}
          >
            {data.vlans.total === 0 ? (
              <Text type="secondary">No VLANs configured. Click Manage to add.</Text>
            ) : (
              <Table
                dataSource={Object.entries(data.vlans.by_purpose).map(([purpose, vlanList]) => ({
                  key: purpose,
                  purpose,
                  vlans: vlanList,
                }))}
                columns={[
                  {
                    title: 'Purpose', dataIndex: 'purpose', width: 130,
                    render: (v: string) => <Tag color={PURPOSE_COLORS[v] || 'default'}>{v}</Tag>,
                  },
                  {
                    title: 'VLANs', dataIndex: 'vlans',
                    render: (vlanList: { vlan_id: number; name: string }[]) => (
                      <Space wrap>
                        {vlanList.map(v => (
                          <Tag key={v.vlan_id}>{v.vlan_id} - {v.name}</Tag>
                        ))}
                      </Space>
                    ),
                  },
                ]}
                pagination={false}
                size="small"
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Authentication Stats (24h)">
            <Row gutter={16}>
              <Col span={8}>
                <Statistic title="Total" value={data.auth_stats_24h.total} />
              </Col>
              <Col span={8}>
                <Statistic
                  title="Success"
                  value={data.auth_stats_24h.success}
                  valueStyle={{ color: '#3f8600' }}
                  prefix={<CheckCircleOutlined />}
                />
              </Col>
              <Col span={8}>
                <Statistic
                  title="Failed"
                  value={data.auth_stats_24h.failed}
                  valueStyle={{ color: data.auth_stats_24h.failed > 0 ? '#cf1322' : undefined }}
                  prefix={data.auth_stats_24h.failed > 0 ? <CloseCircleOutlined /> : undefined}
                />
              </Col>
            </Row>
            {data.auth_stats_24h.total > 0 && (
              <div style={{ marginTop: 16 }}>
                <Text type="secondary">Success Rate</Text>
                <Progress
                  percent={data.auth_stats_24h.success_rate}
                  status={data.auth_stats_24h.success_rate >= 90 ? 'success' : 'normal'}
                />
              </div>
            )}
            {Object.keys(data.auth_stats_24h.by_method).length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Text type="secondary">By Method:</Text>
                <div style={{ marginTop: 8 }}>
                  {Object.entries(data.auth_stats_24h.by_method).map(([method, count]) => (
                    <div key={method} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <Tag color="blue">{method}</Tag>
                      <span>{count as number}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Row 3: Quick Links */}
      <Card title="Configuration Quick Links">
        <Row gutter={[12, 12]}>
          {[
            { label: 'Certificates', path: '/radius/certificates', icon: <SafetyCertificateOutlined /> },
            { label: 'LDAP Servers', path: '/radius/ldap', icon: <GlobalOutlined /> },
            { label: 'Realms', path: '/radius/realms', icon: <GlobalOutlined /> },
            { label: 'NAS Clients', path: '/radius/nas-clients', icon: <KeyOutlined /> },
            { label: 'VLANs', path: '/radius/vlans', icon: <ApartmentOutlined /> },
            { label: 'MAB Devices', path: '/radius/mab-devices', icon: <LaptopOutlined /> },
            { label: 'Dynamic VLAN', path: '/radius/group-vlans', icon: <TeamOutlined /> },
            { label: 'Policies', path: '/policies', icon: <SafetyOutlined /> },
            { label: 'FreeRADIUS', path: '/radius/config', icon: <CloudServerOutlined /> },
            { label: 'Access Tracker', path: '/access-tracker', icon: <SafetyOutlined /> },
          ].map(link => (
            <Col key={link.path} xs={12} sm={8} md={6} lg={4}>
              <Button
                block
                icon={link.icon}
                onClick={() => navigate(link.path)}
                style={{ height: 48 }}
              >
                {link.label}
              </Button>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
}
