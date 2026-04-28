import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, InputNumber, Select, Switch as AntSwitch, message, Popconfirm,
  Alert,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined,
  ArrowUpOutlined, ArrowDownOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../../api';

const { Title, Text } = Typography;

interface GroupVlanMapping {
  id: string;
  group_name: string;
  vlan_id: number;
  priority: number;
  description: string;
  ldap_server_id: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

interface VlanOption {
  vlan_id: number;
  name: string;
  purpose: string;
}

interface LdapOption {
  id: string;
  name: string;
  host: string;
}

export default function GroupVlanMappings() {
  const [mappings, setMappings] = useState<GroupVlanMapping[]>([]);
  const [vlans, setVlans] = useState<VlanOption[]>([]);
  const [ldapServers, setLdapServers] = useState<LdapOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<GroupVlanMapping | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => { loadAll(); }, []);

  const loadAll = () => {
    loadMappings();
    loadVlans();
    loadLdapServers();
  };

  const loadMappings = async () => {
    setLoading(true);
    try {
      const res = await api.get('/group-vlan-mappings');
      setMappings(res.data.items || []);
    } catch { message.error('Failed to load group VLAN mappings'); }
    setLoading(false);
  };

  const loadVlans = async () => {
    try {
      const res = await api.get('/vlans');
      setVlans(res.data.items || []);
    } catch { /* ignore */ }
  };

  const loadLdapServers = async () => {
    try {
      const res = await api.get('/ldap-servers');
      setLdapServers(res.data.items || []);
    } catch { /* ignore */ }
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ enabled: true, priority: 100 });
    setModalOpen(true);
  };

  const openEdit = (item: GroupVlanMapping) => {
    setEditing(item);
    form.setFieldsValue({
      group_name: item.group_name,
      vlan_id: item.vlan_id,
      priority: item.priority,
      description: item.description,
      ldap_server_id: item.ldap_server_id,
      enabled: item.enabled,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editing) {
        await api.put(`/group-vlan-mappings/${editing.id}`, values);
        message.success('Mapping updated');
      } else {
        await api.post('/group-vlan-mappings', values);
        message.success('Mapping created');
      }
      setModalOpen(false);
      loadMappings();
    } catch (err: any) {
      if (err?.response?.data?.detail) message.error(err.response.data.detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/group-vlan-mappings/${id}`);
      message.success('Mapping deleted');
      loadMappings();
    } catch (err) { message.error(extractErrorMessage(err, 'Delete failed')); }
  };

  const getVlanLabel = (vlanId: number) => {
    const v = vlans.find(vl => vl.vlan_id === vlanId);
    return v ? `${vlanId} - ${v.name}` : String(vlanId);
  };

  const getLdapLabel = (serverId: string | null) => {
    if (!serverId) return 'Any';
    const s = ldapServers.find(l => l.id === serverId);
    return s ? s.name : serverId.slice(0, 8);
  };

  const PURPOSE_COLORS: Record<string, string> = {
    corporate: 'blue', guest: 'green', quarantine: 'red',
    iot: 'orange', voip: 'purple', printer: 'cyan',
    remediation: 'gold', management: 'geekblue',
  };

  const columns: ColumnsType<GroupVlanMapping> = [
    {
      title: 'Priority', dataIndex: 'priority', width: 90, sorter: (a, b) => a.priority - b.priority,
      render: (v) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: 'AD/LDAP Group', dataIndex: 'group_name', width: 220,
      render: (v, record) => <a onClick={() => openEdit(record)}>{v}</a>,
    },
    {
      title: 'Assigned VLAN', dataIndex: 'vlan_id', width: 180,
      render: (v) => {
        const vlan = vlans.find(vl => vl.vlan_id === v);
        const purpose = vlan?.purpose;
        return (
          <Space>
            <Tag color={purpose ? PURPOSE_COLORS[purpose] || 'default' : 'default'}>
              {getVlanLabel(v)}
            </Tag>
          </Space>
        );
      },
    },
    {
      title: 'LDAP Server', dataIndex: 'ldap_server_id', width: 140,
      render: (v) => <Text type="secondary">{getLdapLabel(v)}</Text>,
    },
    { title: 'Description', dataIndex: 'description', ellipsis: true, render: (v) => v || '-' },
    {
      title: 'Enabled', dataIndex: 'enabled', width: 80,
      render: (v) => v ? <Badge status="success" text="Yes" /> : <Badge status="default" text="No" />,
    },
    {
      title: 'Actions', key: 'actions', width: 100,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="Delete this mapping?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>Dynamic VLAN Assignment</Title>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Group-to-VLAN Mapping"
        description={
          <span>
            Map AD/LDAP groups to VLANs. When a user authenticates via 802.1X,
            FreeRADIUS checks their group membership and assigns the VLAN with the
            highest priority (lowest number). The switch then places the port in that VLAN.
          </span>
        }
      />
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>Add Mapping</Button>
          <Button icon={<ReloadOutlined />} onClick={loadMappings}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={mappings}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
        />
      </Card>

      <Modal
        title={editing ? 'Edit Group VLAN Mapping' : 'Add Group VLAN Mapping'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={520}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="group_name" label="AD/LDAP Group Name"
            rules={[{ required: true, message: 'Group name is required' }]}
            extra="Exact group name as it appears in AD/LDAP (e.g., IT-Staff, Domain Users)"
          >
            <Input placeholder="e.g., IT-Staff" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="vlan_id" label="Assign to VLAN"
              rules={[{ required: true, message: 'VLAN is required' }]}>
              <Select placeholder="Select VLAN" style={{ width: 220 }}>
                {vlans.map(v => (
                  <Select.Option key={v.vlan_id} value={v.vlan_id}>
                    {v.vlan_id} - {v.name}
                    {v.purpose && ` (${v.purpose})`}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item name="priority" label="Priority"
              rules={[{ required: true }]}
              extra="Lower = higher priority"
            >
              <InputNumber min={1} max={9999} style={{ width: 100 }} />
            </Form.Item>
          </Space>
          <Form.Item name="ldap_server_id" label="LDAP Server (optional)"
            extra="Limit this mapping to a specific LDAP server, or leave empty for any"
          >
            <Select placeholder="Any LDAP server" allowClear style={{ width: '100%' }}>
              {ldapServers.map(s => (
                <Select.Option key={s.id} value={s.id}>
                  {s.name} ({s.host})
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input placeholder="Optional description" />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <AntSwitch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
