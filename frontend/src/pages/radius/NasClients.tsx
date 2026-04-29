import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, Select, Switch as AntSwitch, message, Popconfirm,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../../api';

const { Title } = Typography;
const { TextArea } = Input;

interface NasClient {
  id: string;
  name: string;
  ip_address: string;
  secret: string;
  shortname: string;
  nas_type: string;
  description: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

const NAS_TYPE_COLORS: Record<string, string> = {
  cisco: 'blue',
  juniper: 'green',
  aruba: 'orange',
  other: 'default',
};

export default function NasClients() {
  const [clients, setClients] = useState<NasClient[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingClient, setEditingClient] = useState<NasClient | null>(null);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => { loadClients(); }, []);

  const loadClients = async () => {
    setLoading(true);
    try {
      const res = await api.get('/nas-clients');
      setClients(res.data.items || res.data || []);
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load NAS clients')); }
    setLoading(false);
  };

  const openCreate = () => {
    setEditingClient(null);
    form.resetFields();
    form.setFieldsValue({
      nas_type: 'other',
      enabled: true,
    });
    setModalOpen(true);
  };

  const openEdit = (client: NasClient) => {
    setEditingClient(client);
    form.setFieldsValue({
      name: client.name,
      ip_address: client.ip_address,
      secret: undefined,
      shortname: client.shortname,
      nas_type: client.nas_type,
      description: client.description,
      enabled: client.enabled,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      const payload: any = { ...values };
      // On edit, omit secret if left blank
      if (editingClient && !payload.secret) {
        delete payload.secret;
      }

      if (editingClient) {
        await api.put(`/nas-clients/${editingClient.id}`, payload);
        message.success('NAS client updated');
      } else {
        await api.post('/nas-clients', payload);
        message.success('NAS client created');
      }
      setModalOpen(false);
      loadClients();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to save NAS client'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/nas-clients/${id}`);
      message.success('NAS client deleted');
      loadClients();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Delete failed'));
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      await api.post('/nas-clients/sync-radius');
      message.success('NAS clients synced to FreeRADIUS successfully');
    } catch (err) {
      message.error(extractErrorMessage(err, 'Sync failed'));
    } finally {
      setSyncing(false);
    }
  };

  const columns: ColumnsType<NasClient> = [
    {
      title: 'Name', dataIndex: 'name', width: 200,
      render: (name, record) => (
        <a onClick={() => openEdit(record)}>{name}</a>
      ),
    },
    { title: 'IP Address', dataIndex: 'ip_address', width: 160 },
    { title: 'Shortname', dataIndex: 'shortname', width: 140 },
    {
      title: 'NAS Type', dataIndex: 'nas_type', width: 110,
      render: (v) => <Tag color={NAS_TYPE_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: 'Enabled', dataIndex: 'enabled', width: 80,
      render: (v) => v
        ? <Badge status="success" text="Yes" />
        : <Badge status="default" text="No" />,
    },
    {
      title: 'Actions', key: 'actions', width: 100,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="Delete this NAS client?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>NAS Clients</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            Add NAS Client
          </Button>
          <Button
            icon={<SyncOutlined spin={syncing} />}
            onClick={handleSync}
            loading={syncing}
          >
            Sync to FreeRADIUS
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadClients}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={clients}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      <Modal
        title={editingClient ? 'Edit NAS Client' : 'Add NAS Client'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
            <Input placeholder="e.g., Core Switch 1" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="ip_address" label="IP Address"
              rules={[{ required: true, message: 'IP address is required' }]}>
              <Input placeholder="e.g., 10.0.0.1" style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="shortname" label="Shortname">
              <Input placeholder="e.g., core-sw1" style={{ width: 200 }} />
            </Form.Item>
          </Space>
          <Form.Item name="secret" label="Shared Secret"
            rules={editingClient ? [] : [{ required: true, message: 'Secret is required' }]}>
            <Input.Password placeholder={editingClient ? '(unchanged)' : 'Enter RADIUS shared secret'} />
          </Form.Item>
          <Form.Item name="nas_type" label="NAS Type">
            <Select style={{ width: 200 }}>
              <Select.Option value="cisco">Cisco</Select.Option>
              <Select.Option value="juniper">Juniper</Select.Option>
              <Select.Option value="aruba">Aruba</Select.Option>
              <Select.Option value="other">Other</Select.Option>
            </Select>
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
