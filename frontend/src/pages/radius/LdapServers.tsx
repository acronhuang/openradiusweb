import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, InputNumber, Switch as AntSwitch, message, Popconfirm,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../../api';

const { Title } = Typography;

interface LdapServer {
  id: string;
  name: string;
  host: string;
  port: number;
  use_tls: boolean;
  use_starttls: boolean;
  bind_dn: string;
  base_dn: string;
  user_search_filter: string;
  group_search_filter: string;
  connect_timeout_seconds: number;
  search_timeout_seconds: number;
  priority: number;
  enabled: boolean;
  last_test_result: string | null;
  last_test_at: string | null;
  created_at: string;
  updated_at: string;
}

export default function LdapServers() {
  const [servers, setServers] = useState<LdapServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<LdapServer | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [form] = Form.useForm();

  useEffect(() => { loadServers(); }, []);

  const loadServers = async () => {
    setLoading(true);
    try {
      const res = await api.get('/ldap-servers');
      setServers(res.data.items || res.data || []);
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load LDAP servers')); }
    setLoading(false);
  };

  const openCreate = () => {
    setEditingServer(null);
    form.resetFields();
    form.setFieldsValue({
      port: 389,
      use_tls: false,
      use_starttls: false,
      user_search_filter: '(sAMAccountName={username})',
      group_search_filter: '(member={dn})',
      connect_timeout_seconds: 5,
      search_timeout_seconds: 10,
      priority: 100,
      enabled: true,
    });
    setModalOpen(true);
  };

  const openEdit = (server: LdapServer) => {
    setEditingServer(server);
    form.setFieldsValue({
      name: server.name,
      host: server.host,
      port: server.port,
      use_tls: server.use_tls,
      use_starttls: server.use_starttls,
      bind_dn: server.bind_dn,
      bind_password: undefined,
      base_dn: server.base_dn,
      user_search_filter: server.user_search_filter,
      group_search_filter: server.group_search_filter,
      connect_timeout_seconds: server.connect_timeout_seconds,
      search_timeout_seconds: server.search_timeout_seconds,
      priority: server.priority,
      enabled: server.enabled,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      const payload: any = { ...values };
      // On edit, omit bind_password if left blank (meaning "unchanged")
      if (editingServer && !payload.bind_password) {
        delete payload.bind_password;
      }

      if (editingServer) {
        await api.put(`/ldap-servers/${editingServer.id}`, payload);
        message.success('LDAP server updated');
      } else {
        await api.post('/ldap-servers', payload);
        message.success('LDAP server created');
      }
      setModalOpen(false);
      loadServers();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to save LDAP server'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/ldap-servers/${id}`);
      message.success('LDAP server deleted');
      loadServers();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Delete failed'));
    }
  };

  const handleTest = async (id: string) => {
    setTestingId(id);
    try {
      const res = await api.post(`/ldap-servers/${id}/test`);
      const r = res.data;
      if (r.success) {
        message.success(
          `Connection successful - Connect: ${r.connect_time_ms}ms, Bind: ${r.bind_time_ms}ms, ` +
          `Results: ${r.search_result_count}, Type: ${r.server_type || 'unknown'}`
        );
      } else {
        message.error(`Connection failed: ${r.error || 'Unknown error'}`);
      }
      loadServers();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Test failed'));
    } finally {
      setTestingId(null);
    }
  };

  const columns: ColumnsType<LdapServer> = [
    {
      title: 'Name', dataIndex: 'name', width: 180,
      render: (name, record) => (
        <a onClick={() => openEdit(record)}>{name}</a>
      ),
    },
    {
      title: 'Host:Port', key: 'hostport', width: 200,
      render: (_, record) => `${record.host}:${record.port}`,
    },
    {
      title: 'TLS', dataIndex: 'use_tls', width: 70,
      render: (v) => v ? <Tag color="green">TLS</Tag> : <Tag>No</Tag>,
    },
    { title: 'Base DN', dataIndex: 'base_dn', ellipsis: true },
    {
      title: 'Priority', dataIndex: 'priority', width: 80,
      sorter: (a, b) => a.priority - b.priority,
      defaultSortOrder: 'ascend',
    },
    {
      title: 'Last Test', key: 'last_test', width: 100,
      render: (_, record) => {
        if (!record.last_test_at) return <Badge status="default" text="Never" />;
        return record.last_test_result === 'success'
          ? <Badge status="success" text="Success" />
          : <Badge status="error" text="Failed" />;
      },
    },
    {
      title: 'Enabled', dataIndex: 'enabled', width: 80,
      render: (v) => v
        ? <Badge status="success" text="Yes" />
        : <Badge status="default" text="No" />,
    },
    {
      title: 'Actions', key: 'actions', width: 150,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Button
            size="small"
            icon={<ApiOutlined />}
            loading={testingId === record.id}
            onClick={() => handleTest(record.id)}
            title="Test Connection"
          />
          <Popconfirm title="Delete this LDAP server?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>LDAP Servers</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            Add LDAP Server
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadServers}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={servers}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      <Modal
        title={editingServer ? 'Edit LDAP Server' : 'Add LDAP Server'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={640}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
            <Input placeholder="e.g., Corporate AD" />
          </Form.Item>

          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="host" label="Host" rules={[{ required: true, message: 'Host is required' }]}>
              <Input placeholder="ldap.example.com" style={{ width: 300 }} />
            </Form.Item>
            <Form.Item name="port" label="Port">
              <InputNumber min={1} max={65535} style={{ width: 100 }} />
            </Form.Item>
          </Space>

          <Space>
            <Form.Item name="use_tls" label="Use TLS" valuePropName="checked">
              <AntSwitch />
            </Form.Item>
            <Form.Item name="use_starttls" label="Use STARTTLS" valuePropName="checked">
              <AntSwitch />
            </Form.Item>
          </Space>

          <Form.Item name="bind_dn" label="Bind DN" rules={[{ required: true, message: 'Bind DN is required' }]}>
            <Input placeholder="cn=admin,dc=example,dc=com" />
          </Form.Item>
          <Form.Item
            name="bind_password"
            label="Bind Password"
            rules={editingServer
              ? []
              : [{ required: true, message: 'Bind Password is required' }]}
          >
            <Input.Password placeholder={editingServer ? '(unchanged)' : 'Enter password'} />
          </Form.Item>
          <Form.Item name="base_dn" label="Base DN" rules={[{ required: true, message: 'Base DN is required' }]}>
            <Input placeholder="dc=example,dc=com" />
          </Form.Item>

          <Form.Item name="user_search_filter" label="User Search Filter">
            <Input placeholder="(sAMAccountName={username})" />
          </Form.Item>
          <Form.Item name="group_search_filter" label="Group Search Filter">
            <Input placeholder="(member={dn})" />
          </Form.Item>

          <Space>
            <Form.Item name="connect_timeout_seconds" label="Connect Timeout (s)">
              <InputNumber min={1} max={60} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="search_timeout_seconds" label="Search Timeout (s)">
              <InputNumber min={1} max={120} style={{ width: 120 }} />
            </Form.Item>
          </Space>

          <Space>
            <Form.Item name="priority" label="Priority">
              <InputNumber min={1} max={10000} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="enabled" label="Enabled" valuePropName="checked">
              <AntSwitch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
