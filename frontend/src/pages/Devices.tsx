import React, { useState, useEffect } from 'react';
import { Table, Tag, Input, Select, Button, Space, Typography, Card, message } from 'antd';
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '../api';

const { Title } = Typography;

interface Device {
  id: string;
  mac_address: string;
  ip_address: string;
  hostname: string;
  device_type: string;
  os_family: string;
  vendor: string;
  status: string;
  risk_score: number;
  last_seen: string;
}

export default function Devices() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');

  useEffect(() => { loadDevices(); }, [page]);

  const loadDevices = async () => {
    setLoading(true);
    try {
      const res = await api.get('/devices', {
        params: { page, page_size: 50, search },
      });
      setDevices(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch { message.error('Failed to load devices'); }
    setLoading(false);
  };

  const columns: ColumnsType<Device> = [
    { title: 'MAC Address', dataIndex: 'mac_address', width: 160 },
    { title: 'IP Address', dataIndex: 'ip_address', width: 140 },
    { title: 'Hostname', dataIndex: 'hostname' },
    { title: 'Type', dataIndex: 'device_type', width: 100,
      render: (v) => <Tag>{v || 'unknown'}</Tag> },
    { title: 'OS', dataIndex: 'os_family', width: 100 },
    { title: 'Vendor', dataIndex: 'vendor', width: 120 },
    { title: 'Status', dataIndex: 'status', width: 100,
      render: (v) => (
        <Tag color={v === 'online' ? 'green' : v === 'quarantined' ? 'red' : 'default'}>
          {v}
        </Tag>
      ),
    },
    { title: 'Risk', dataIndex: 'risk_score', width: 80,
      render: (v) => (
        <Tag color={v >= 70 ? 'red' : v >= 40 ? 'orange' : 'green'}>{v}</Tag>
      ),
    },
    { title: 'Last Seen', dataIndex: 'last_seen', width: 160,
      render: (v) => v ? new Date(v).toLocaleString() : '-' },
  ];

  return (
    <div>
      <Title level={4}>Devices</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Input placeholder="Search MAC, IP, hostname..." prefix={<SearchOutlined />}
            value={search} onChange={(e) => setSearch(e.target.value)}
            onPressEnter={loadDevices} style={{ width: 300 }} />
          <Button icon={<ReloadOutlined />} onClick={loadDevices}>Refresh</Button>
        </Space>
        <Table columns={columns} dataSource={devices} rowKey="id"
          loading={loading} size="small"
          pagination={{ current: page, total, pageSize: 50,
            onChange: setPage, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>
    </div>
  );
}
