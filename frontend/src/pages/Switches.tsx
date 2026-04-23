import React, { useState, useEffect } from 'react';
import { Table, Tag, Button, Space, Typography, Card, Badge, message } from 'antd';
import { ReloadOutlined, ApartmentOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '../api';

const { Title } = Typography;

interface Switch {
  id: string;
  ip_address: string;
  hostname: string;
  vendor: string;
  model: string;
  device_type: string;
  enabled: boolean;
  last_polled: string;
}

export default function Switches() {
  const [switches, setSwitches] = useState<Switch[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);

  useEffect(() => { loadSwitches(); }, []);

  const loadSwitches = async () => {
    setLoading(true);
    try {
      const res = await api.get('/network-devices', { params: { page_size: 100 } });
      setSwitches(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch { message.error('Failed to load switches'); }
    setLoading(false);
  };

  const columns: ColumnsType<Switch> = [
    { title: 'IP Address', dataIndex: 'ip_address', width: 140 },
    { title: 'Hostname', dataIndex: 'hostname' },
    { title: 'Vendor', dataIndex: 'vendor', width: 120 },
    { title: 'Model', dataIndex: 'model', width: 120 },
    { title: 'Type', dataIndex: 'device_type', width: 100,
      render: (v) => <Tag color="blue">{v}</Tag> },
    { title: 'Status', dataIndex: 'enabled', width: 80,
      render: (v) => <Badge status={v ? 'success' : 'default'} text={v ? 'Active' : 'Disabled'} /> },
    { title: 'Last Polled', dataIndex: 'last_polled', width: 160,
      render: (v) => v ? new Date(v).toLocaleString() : 'Never' },
    { title: 'Actions', width: 120,
      render: (_, record) => (
        <Button size="small" icon={<ApartmentOutlined />}>Ports</Button>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>Network Devices</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button icon={<ReloadOutlined />} onClick={loadSwitches}>Refresh</Button>
        </Space>
        <Table columns={columns} dataSource={switches} rowKey="id"
          loading={loading} size="small"
          pagination={{ total, pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>
    </div>
  );
}
