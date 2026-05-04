import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, message,
  Modal, Form, Input, Select, InputNumber, Popconfirm,
} from 'antd';
import {
  ReloadOutlined, ApartmentOutlined, PlusOutlined, DeleteOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../api';

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

// Vendor list mirrors VENDOR_DEVICE_TYPE in
// services/switch_mgmt/ssh_manager.py — anything outside this set
// would silently fall back to "cisco_ios" which is rarely what the
// operator wants. Keep these in sync if either side changes.
const VENDOR_OPTIONS = [
  { value: 'cisco', label: 'Cisco IOS' },
  { value: 'cisco_xe', label: 'Cisco IOS-XE' },
  { value: 'cisco_nxos', label: 'Cisco NX-OS' },
  { value: 'aruba', label: 'Aruba OS' },
  { value: 'aruba_cx', label: 'Aruba CX' },
  { value: 'juniper', label: 'Juniper Junos' },
  { value: 'fortinet', label: 'FortiOS' },
  { value: 'hp_procurve', label: 'HP ProCurve' },
  { value: 'dell', label: 'Dell Force10' },
  { value: 'extreme', label: 'Extreme EXOS' },
];

// device_type matches the Pydantic regex on NetworkDeviceCreate.
const DEVICE_TYPE_OPTIONS = [
  { value: 'switch', label: 'Switch' },
  { value: 'router', label: 'Router' },
  { value: 'ap', label: 'Access Point' },
  { value: 'firewall', label: 'Firewall' },
];

export default function Switches() {
  const [switches, setSwitches] = useState<Switch[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => { loadSwitches(); }, []);

  const loadSwitches = async () => {
    setLoading(true);
    try {
      const res = await api.get('/network-devices', { params: { page_size: 100 } });
      setSwitches(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load switches')); }
    setLoading(false);
  };

  const openAdd = () => {
    form.resetFields();
    form.setFieldsValue({
      device_type: 'switch',
      management_protocol: 'snmp',
      snmp_version: 'v2c',
      poll_interval_seconds: 300,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      // Ant's InputNumber returns number; backend wants int. Strings
      // for the secret fields (snmp_community / ssh_password) — empty
      // becomes undefined so the backend sees it as "not provided"
      // instead of a literal empty string ciphertext.
      const payload: Record<string, unknown> = { ...values };
      for (const k of ['snmp_community', 'ssh_username', 'ssh_password', 'hostname', 'model'] as const) {
        if (payload[k] === '') delete payload[k];
      }
      await api.post('/network-devices', payload);
      message.success(`Network device ${values.ip_address} added`);
      setModalOpen(false);
      loadSwitches();
    } catch (err) {
      // Skip the noise from form validation errors; show only API errors
      if ((err as { errorFields?: unknown[] })?.errorFields) return;
      message.error(extractErrorMessage(err, 'Failed to add network device'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string, ip: string) => {
    try {
      await api.delete(`/network-devices/${id}`);
      message.success(`${ip} removed`);
      loadSwitches();
    } catch (err) { message.error(extractErrorMessage(err, 'Delete failed')); }
  };

  const columns: ColumnsType<Switch> = [
    { title: 'IP Address', dataIndex: 'ip_address', width: 140 },
    { title: 'Hostname', dataIndex: 'hostname', render: (v) => v || '-' },
    { title: 'Vendor', dataIndex: 'vendor', width: 120, render: (v) => v || '-' },
    { title: 'Model', dataIndex: 'model', width: 120, render: (v) => v || '-' },
    { title: 'Type', dataIndex: 'device_type', width: 100,
      render: (v) => <Tag color="blue">{v}</Tag> },
    { title: 'Status', dataIndex: 'enabled', width: 80,
      render: (v) => <Badge status={v ? 'success' : 'default'} text={v ? 'Active' : 'Disabled'} /> },
    { title: 'Last Polled', dataIndex: 'last_polled', width: 160,
      render: (v) => v ? new Date(v).toLocaleString() : 'Never' },
    { title: 'Actions', width: 130,
      render: (_, record) => (
        <Space size="small">
          <Button size="small" icon={<ApartmentOutlined />}>Ports</Button>
          <Popconfirm
            title={`Remove ${record.ip_address}?`}
            description="This deletes the device + all its discovered ports."
            onConfirm={() => handleDelete(record.id, record.ip_address)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>Network Devices</Title>
      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>Add Device</Button>
          <Button icon={<ReloadOutlined />} onClick={loadSwitches}>Refresh</Button>
        </Space>
        <Table columns={columns} dataSource={switches} rowKey="id"
          loading={loading} size="small"
          pagination={{ total, pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      <Modal
        title="Add Network Device"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        okText="Add"
        width={620}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item
              name="ip_address" label="IP Address"
              rules={[{ required: true, message: 'IP address is required' }]}
              style={{ width: 200 }}
            >
              <Input placeholder="10.0.0.1" />
            </Form.Item>
            <Form.Item name="hostname" label="Hostname" style={{ width: 280 }}>
              <Input placeholder="core-sw-1" />
            </Form.Item>
          </Space>

          <Space style={{ display: 'flex' }} align="start">
            <Form.Item
              name="device_type" label="Type"
              rules={[{ required: true }]}
              style={{ width: 130 }}
            >
              <Select options={DEVICE_TYPE_OPTIONS} />
            </Form.Item>
            <Form.Item name="vendor" label="Vendor" style={{ width: 180 }}>
              <Select
                options={VENDOR_OPTIONS}
                showSearch
                allowClear
                placeholder="Select vendor"
              />
            </Form.Item>
            <Form.Item name="model" label="Model" style={{ width: 170 }}>
              <Input placeholder="Catalyst 9300" />
            </Form.Item>
          </Space>

          <Form.Item
            name="management_protocol" label="Management Protocol"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: 'snmp', label: 'SNMP only' },
                { value: 'ssh', label: 'SSH only' },
                { value: 'snmp+ssh', label: 'SNMP + SSH (poll via SNMP, mutate via SSH)' },
              ]}
            />
          </Form.Item>

          {/* SNMP block */}
          <Card size="small" title="SNMP" style={{ marginBottom: 12 }}>
            <Space style={{ display: 'flex' }} align="start">
              <Form.Item name="snmp_version" label="Version" style={{ width: 100 }}>
                <Select options={[
                  { value: 'v1', label: 'v1' },
                  { value: 'v2c', label: 'v2c' },
                  { value: 'v3', label: 'v3' },
                ]} />
              </Form.Item>
              <Form.Item
                name="snmp_community" label="Community String"
                style={{ width: 300 }}
              >
                <Input.Password
                  placeholder="public"
                  autoComplete="off"
                  visibilityToggle
                />
              </Form.Item>
            </Space>
          </Card>

          {/* SSH block — PR #100 added these to the API. Empty = no
              SSH-based actions (port-bounce etc.) work for this device. */}
          <Card size="small" title="SSH credentials (for port-bounce / VLAN-via-CLI)" style={{ marginBottom: 12 }}>
            <Space style={{ display: 'flex' }} align="start">
              <Form.Item name="ssh_username" label="Username" style={{ width: 200 }}>
                <Input placeholder="netadmin" autoComplete="off" />
              </Form.Item>
              <Form.Item name="ssh_password" label="Password" style={{ width: 280 }}>
                <Input.Password
                  placeholder="leave empty to skip / use SSH key auth"
                  autoComplete="new-password"
                  visibilityToggle
                />
              </Form.Item>
            </Space>
          </Card>

          <Form.Item
            name="poll_interval_seconds" label="SNMP Poll Interval (seconds)"
            rules={[{ required: true }]}
          >
            <InputNumber min={30} max={86400} style={{ width: 180 }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
