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

interface LdapServerOption {
  id: string;
  name: string;
}

interface Realm {
  id: string;
  name: string;
  description: string;
  realm_type: 'local' | 'proxy' | 'reject';
  strip_username: boolean;
  ldap_server_id: string | null;
  ldap_server_name: string | null;
  proxy_host: string | null;
  proxy_port: number | null;
  proxy_secret: string | null;
  auth_types_allowed: string[];
  default_vlan: number | null;
  priority: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

const REALM_TYPE_COLORS: Record<string, string> = {
  local: 'blue',
  proxy: 'orange',
  reject: 'red',
};

const AUTH_TYPE_OPTIONS = [
  { value: 'EAP-TLS', label: 'EAP-TLS' },
  { value: 'PEAP', label: 'PEAP' },
  { value: 'EAP-TTLS', label: 'EAP-TTLS' },
  { value: 'MAB', label: 'MAB' },
];

export default function Realms() {
  const [realms, setRealms] = useState<Realm[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingRealm, setEditingRealm] = useState<Realm | null>(null);
  const [saving, setSaving] = useState(false);
  const [ldapServers, setLdapServers] = useState<LdapServerOption[]>([]);
  const [realmType, setRealmType] = useState<string>('local');
  const [form] = Form.useForm();

  useEffect(() => { loadRealms(); }, []);

  const loadRealms = async () => {
    setLoading(true);
    try {
      const res = await api.get('/radius/realms');
      setRealms(res.data.items || res.data || []);
    } catch { message.error('Failed to load realms'); }
    setLoading(false);
  };

  const loadLdapServers = async () => {
    try {
      const res = await api.get('/ldap-servers');
      const items = res.data.items || res.data || [];
      setLdapServers(items.map((s: any) => ({ id: s.id, name: s.name })));
    } catch { message.error('Failed to load realms'); }
  };

  const openCreate = () => {
    setEditingRealm(null);
    form.resetFields();
    form.setFieldsValue({
      realm_type: 'local',
      strip_username: true,
      auth_types_allowed: ['PEAP'],
      priority: 100,
      enabled: true,
    });
    setRealmType('local');
    loadLdapServers();
    setModalOpen(true);
  };

  const openEdit = (realm: Realm) => {
    setEditingRealm(realm);
    form.setFieldsValue({
      name: realm.name,
      description: realm.description,
      realm_type: realm.realm_type,
      strip_username: realm.strip_username,
      ldap_server_id: realm.ldap_server_id,
      proxy_host: realm.proxy_host,
      proxy_port: realm.proxy_port,
      proxy_secret: undefined,
      auth_types_allowed: realm.auth_types_allowed || [],
      default_vlan: realm.default_vlan,
      priority: realm.priority,
      enabled: realm.enabled,
    });
    setRealmType(realm.realm_type);
    loadLdapServers();
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      const payload: any = {
        name: values.name,
        description: values.description || '',
        realm_type: values.realm_type,
        strip_username: values.strip_username,
        auth_types_allowed: values.auth_types_allowed || [],
        default_vlan: values.default_vlan || null,
        priority: values.priority,
        enabled: values.enabled,
      };

      if (values.realm_type === 'local') {
        payload.ldap_server_id = values.ldap_server_id || null;
        payload.proxy_host = null;
        payload.proxy_port = null;
      } else if (values.realm_type === 'proxy') {
        payload.ldap_server_id = null;
        payload.proxy_host = values.proxy_host;
        payload.proxy_port = values.proxy_port;
        if (values.proxy_secret) {
          payload.proxy_secret = values.proxy_secret;
        }
      } else {
        // reject
        payload.ldap_server_id = null;
        payload.proxy_host = null;
        payload.proxy_port = null;
      }

      if (editingRealm) {
        await api.put(`/radius/realms/${editingRealm.id}`, payload);
        message.success('Realm updated');
      } else {
        await api.post('/radius/realms', payload);
        message.success('Realm created');
      }
      setModalOpen(false);
      loadRealms();
    } catch (err: any) {
      if (err?.response?.data?.detail) {
        message.error(err.response.data.detail);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/radius/realms/${id}`);
      message.success('Realm deleted');
      loadRealms();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Delete failed'));
    }
  };

  const handleRealmTypeChange = (value: string) => {
    setRealmType(value);
  };

  const columns: ColumnsType<Realm> = [
    {
      title: 'Name', dataIndex: 'name', width: 200,
      render: (name, record) => (
        <a onClick={() => openEdit(record)}>{name}</a>
      ),
    },
    {
      title: 'Type', dataIndex: 'realm_type', width: 100,
      render: (v) => <Tag color={REALM_TYPE_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: 'LDAP Server', key: 'ldap', width: 180,
      render: (_, record) => record.ldap_server_name || (record.ldap_server_id ? record.ldap_server_id : '-'),
    },
    {
      title: 'Priority', dataIndex: 'priority', width: 80,
      sorter: (a, b) => a.priority - b.priority,
      defaultSortOrder: 'ascend',
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
          <Popconfirm title="Delete this realm?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>Realms</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            Add Realm
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadRealms}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={realms}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      <Modal
        title={editingRealm ? 'Edit Realm' : 'Add Realm'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={640}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
            <Input placeholder="e.g., example.com" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <TextArea rows={2} placeholder="Realm description" />
          </Form.Item>

          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="realm_type" label="Realm Type" rules={[{ required: true }]}>
              <Select style={{ width: 160 }} onChange={handleRealmTypeChange}>
                <Select.Option value="local">Local</Select.Option>
                <Select.Option value="proxy">Proxy</Select.Option>
                <Select.Option value="reject">Reject</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item name="strip_username" label="Strip Username" valuePropName="checked">
              <AntSwitch />
            </Form.Item>
          </Space>

          {realmType === 'local' && (
            <Form.Item name="ldap_server_id" label="LDAP Server">
              <Select placeholder="Select LDAP server" allowClear style={{ width: '100%' }}>
                {ldapServers.map((s) => (
                  <Select.Option key={s.id} value={s.id}>{s.name}</Select.Option>
                ))}
              </Select>
            </Form.Item>
          )}

          {realmType === 'proxy' && (
            <>
              <Space style={{ display: 'flex' }} align="start">
                <Form.Item name="proxy_host" label="Proxy Host"
                  rules={[{ required: true, message: 'Proxy host is required' }]}>
                  <Input placeholder="proxy.example.com" style={{ width: 300 }} />
                </Form.Item>
                <Form.Item name="proxy_port" label="Proxy Port">
                  <InputNumber min={1} max={65535} placeholder="1812" style={{ width: 120 }} />
                </Form.Item>
              </Space>
              <Form.Item name="proxy_secret" label="Proxy Secret">
                <Input.Password placeholder={editingRealm ? '(unchanged)' : 'Enter shared secret'} />
              </Form.Item>
            </>
          )}

          <Form.Item name="auth_types_allowed" label="Allowed Auth Types">
            <Select mode="multiple" placeholder="Select auth types" options={AUTH_TYPE_OPTIONS} />
          </Form.Item>

          <Space>
            <Form.Item name="default_vlan" label="Default VLAN">
              <InputNumber min={1} max={4094} placeholder="Optional" style={{ width: 120 }} />
            </Form.Item>
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
