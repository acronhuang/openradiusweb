import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, InputNumber, Select, Switch as AntSwitch, message, Popconfirm,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../../api';

const { Title } = Typography;
const { TextArea } = Input;

interface Vlan {
  id: string;
  vlan_id: number;
  name: string;
  description: string;
  purpose: string;
  subnet: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

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

const PURPOSE_OPTIONS = [
  { value: 'corporate', label: 'Corporate' },
  { value: 'guest', label: 'Guest' },
  { value: 'quarantine', label: 'Quarantine' },
  { value: 'iot', label: 'IoT' },
  { value: 'voip', label: 'VoIP' },
  { value: 'printer', label: 'Printer' },
  { value: 'remediation', label: 'Remediation' },
  { value: 'management', label: 'Management' },
];

export default function VlanManagement() {
  const [vlans, setVlans] = useState<Vlan[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingVlan, setEditingVlan] = useState<Vlan | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => { loadVlans(); }, []);

  const loadVlans = async () => {
    setLoading(true);
    try {
      const res = await api.get('/vlans');
      setVlans(res.data.items || []);
    } catch { message.error('Failed to load VLANs'); }
    setLoading(false);
  };

  const openCreate = () => {
    setEditingVlan(null);
    form.resetFields();
    form.setFieldsValue({ enabled: true });
    setModalOpen(true);
  };

  const openEdit = (vlan: Vlan) => {
    setEditingVlan(vlan);
    form.setFieldsValue({
      vlan_id: vlan.vlan_id,
      name: vlan.name,
      description: vlan.description,
      purpose: vlan.purpose,
      subnet: vlan.subnet,
      enabled: vlan.enabled,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editingVlan) {
        await api.put(`/vlans/${editingVlan.id}`, values);
        message.success('VLAN updated');
      } else {
        await api.post('/vlans', values);
        message.success('VLAN created');
      }
      setModalOpen(false);
      loadVlans();
    } catch (err: any) {
      if (err?.response?.data?.detail) message.error(err.response.data.detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/vlans/${id}`);
      message.success('VLAN deleted');
      loadVlans();
    } catch (err) { message.error(extractErrorMessage(err, 'Delete failed')); }
  };

  const columns: ColumnsType<Vlan> = [
    {
      title: 'VLAN ID', dataIndex: 'vlan_id', width: 100, sorter: (a, b) => a.vlan_id - b.vlan_id,
      render: (v) => <strong>{v}</strong>,
    },
    {
      title: 'Name', dataIndex: 'name', width: 180,
      render: (name, record) => <a onClick={() => openEdit(record)}>{name}</a>,
    },
    {
      title: 'Purpose', dataIndex: 'purpose', width: 130,
      render: (v) => v ? <Tag color={PURPOSE_COLORS[v] || 'default'}>{v}</Tag> : '-',
    },
    { title: 'Subnet', dataIndex: 'subnet', width: 160, render: (v) => v || '-' },
    { title: 'Description', dataIndex: 'description', ellipsis: true },
    {
      title: 'Enabled', dataIndex: 'enabled', width: 80,
      render: (v) => v ? <Badge status="success" text="Yes" /> : <Badge status="default" text="No" />,
    },
    {
      title: 'Actions', key: 'actions', width: 100,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="Delete this VLAN?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>VLAN Management</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>Add VLAN</Button>
          <Button icon={<ReloadOutlined />} onClick={loadVlans}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={vlans}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      <Modal
        title={editingVlan ? 'Edit VLAN' : 'Add VLAN'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={520}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="vlan_id" label="VLAN ID"
              rules={[{ required: true, message: 'VLAN ID is required' }]}>
              <InputNumber min={1} max={4094} placeholder="e.g., 10" style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="name" label="Name"
              rules={[{ required: true, message: 'Name is required' }]}>
              <Input placeholder="e.g., Corporate" style={{ width: 250 }} />
            </Form.Item>
          </Space>
          <Form.Item name="purpose" label="Purpose">
            <Select placeholder="Select purpose" allowClear style={{ width: 200 }}>
              {PURPOSE_OPTIONS.map(o => (
                <Select.Option key={o.value} value={o.value}>{o.label}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="subnet" label="Subnet (CIDR)">
            <Input placeholder="e.g., 10.10.10.0/24" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <TextArea rows={2} placeholder="Optional description" />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <AntSwitch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
