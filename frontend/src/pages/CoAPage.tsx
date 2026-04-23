import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Input, Select,
  Modal, message, Descriptions, Badge, Popconfirm, Tabs, Form,
} from 'antd';
import {
  DisconnectOutlined, ReloadOutlined, SwapOutlined,
  WarningOutlined, SearchOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '../api';

const { Title, Text } = Typography;

interface Session {
  id: string;
  session_id: string;
  username: string;
  calling_station_id: string;
  nas_ip: string;
  assigned_vlan: number;
  started_at: string;
  device_hostname: string;
  switch_hostname: string;
  switch_vendor: string;
}

function VlanSelect() {
  const [vlans, setVlans] = useState<{vlan_id: number; name: string}[]>([]);
  useEffect(() => {
    api.get('/vlans').then(res => setVlans(res.data.items || [])).catch(() => {});
  }, []);
  return (
    <Form.Item name="vlan_id" label="New VLAN" rules={[{ required: true }]}>
      <Select placeholder="Select target VLAN">
        {vlans.map(v => (
          <Select.Option key={v.vlan_id} value={v.vlan_id}>
            {v.vlan_id} - {v.name}
          </Select.Option>
        ))}
      </Select>
    </Form.Item>
  );
}

export default function CoAPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [coaHistory, setCoaHistory] = useState<any[]>([]);
  const [coaForm] = Form.useForm();
  const [showCoaModal, setShowCoaModal] = useState(false);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);

  useEffect(() => { loadSessions(); }, []);

  const loadSessions = async () => {
    setLoading(true);
    try {
      const res = await api.get('/coa/active-sessions', { params: { page_size: 100 } });
      setSessions(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch { message.error('Operation failed'); }
    setLoading(false);
  };

  const loadHistory = async () => {
    try {
      const res = await api.get('/coa/history', { params: { page_size: 50 } });
      setCoaHistory(res.data.items || []);
    } catch { message.error('Operation failed'); }
  };

  const sendCoA = async (session: Session, action: string, vlanId?: number) => {
    try {
      await api.post('/coa/by-session', {
        session_id: session.session_id,
        action,
        vlan_id: vlanId,
        reason: `Manual CoA from web UI`,
      });
      message.success(`CoA ${action} sent to ${session.calling_station_id}`);
      setTimeout(loadSessions, 3000);
    } catch {
      message.error('CoA request failed');
    }
  };

  const columns: ColumnsType<Session> = [
    { title: 'Username', dataIndex: 'username', width: 140 },
    { title: 'MAC Address', dataIndex: 'calling_station_id', width: 150 },
    { title: 'Device', dataIndex: 'device_hostname', width: 140,
      render: (v) => v || '-' },
    { title: 'Switch', dataIndex: 'switch_hostname', width: 140,
      render: (v, r) => v || r.nas_ip },
    { title: 'VLAN', dataIndex: 'assigned_vlan', width: 80,
      render: (v) => <Tag color="blue">{v}</Tag> },
    { title: 'Connected Since', dataIndex: 'started_at', width: 160,
      render: (v) => v ? new Date(v).toLocaleString() : '-' },
    { title: 'Actions', width: 280,
      render: (_, session) => (
        <Space>
          <Popconfirm title="Force re-authentication?" onConfirm={() => sendCoA(session, 'reauthenticate')}>
            <Button size="small" icon={<ReloadOutlined />} type="primary">
              Reauth
            </Button>
          </Popconfirm>
          <Button size="small" icon={<SwapOutlined />}
            onClick={() => { setSelectedSession(session); setShowCoaModal(true); }}>
            Change VLAN
          </Button>
          <Popconfirm title="Disconnect this device?" okType="danger"
            onConfirm={() => sendCoA(session, 'disconnect')}>
            <Button size="small" icon={<DisconnectOutlined />} danger>
              Disconnect
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>Change of Authorization (CoA)</Title>
      <Tabs items={[
        {
          key: 'sessions',
          label: 'Active Sessions',
          children: (
            <Card>
              <Space style={{ marginBottom: 16 }}>
                <Button icon={<ReloadOutlined />} onClick={loadSessions}>Refresh</Button>
                <Text type="secondary">{total} active sessions</Text>
              </Space>
              <Table columns={columns} dataSource={sessions} rowKey="id"
                loading={loading} size="small"
                pagination={{ total, pageSize: 50 }}
              />
            </Card>
          ),
        },
        {
          key: 'history',
          label: 'CoA History',
          children: (
            <Card>
              <Button icon={<ReloadOutlined />} onClick={loadHistory}
                style={{ marginBottom: 16 }}>Refresh</Button>
              <Table dataSource={coaHistory} rowKey="id" size="small"
                columns={[
                  { title: 'Time', dataIndex: 'timestamp', width: 160,
                    render: (v: string) => v ? new Date(v).toLocaleString() : '-' },
                  { title: 'Action', dataIndex: ['details', 'action'], width: 120 },
                  { title: 'Target', dataIndex: 'message', ellipsis: true },
                  { title: 'Result', dataIndex: ['details', 'success'], width: 80,
                    render: (v: boolean) => (
                      <Tag color={v ? 'green' : 'red'}>{v ? 'Success' : 'Failed'}</Tag>
                    ),
                  },
                ]}
              />
            </Card>
          ),
        },
      ]} />

      <Modal title="Change VLAN" open={showCoaModal}
        onCancel={() => setShowCoaModal(false)}
        onOk={async () => {
          const values = await coaForm.validateFields();
          if (selectedSession) {
            await sendCoA(selectedSession, 'vlan_change', values.vlan_id);
            setShowCoaModal(false);
          }
        }}>
        {selectedSession && (
          <>
            <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="Device">{selectedSession.calling_station_id}</Descriptions.Item>
              <Descriptions.Item label="Current VLAN">{selectedSession.assigned_vlan}</Descriptions.Item>
            </Descriptions>
            <Form form={coaForm}>
              <VlanSelect />
            </Form>
          </>
        )}
      </Modal>
    </div>
  );
}
