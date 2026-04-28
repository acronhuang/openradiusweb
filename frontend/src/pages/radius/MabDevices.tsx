import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, Select, Switch as AntSwitch, DatePicker, message, Popconfirm,
  Upload,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined,
  ImportOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import api, { extractErrorMessage } from '../../api';

const { Title } = Typography;
const { TextArea } = Input;

interface MabDevice {
  id: string;
  mac_address: string;
  name: string;
  description: string;
  device_type: string;
  assigned_vlan_id: number | null;
  enabled: boolean;
  expiry_date: string | null;
  created_at: string;
  updated_at: string;
}

interface VlanOption {
  vlan_id: number;
  name: string;
  purpose: string;
}

const DEVICE_TYPES = [
  { value: 'printer', label: 'Printer' },
  { value: 'camera', label: 'Camera' },
  { value: 'iot', label: 'IoT Sensor' },
  { value: 'phone', label: 'IP Phone' },
  { value: 'sensor', label: 'Sensor' },
  { value: 'ap', label: 'Access Point' },
  { value: 'other', label: 'Other' },
];

const TYPE_COLORS: Record<string, string> = {
  printer: 'cyan',
  camera: 'orange',
  iot: 'green',
  phone: 'purple',
  sensor: 'blue',
  ap: 'geekblue',
  other: 'default',
};

export default function MabDevices() {
  const [devices, setDevices] = useState<MabDevice[]>([]);
  const [vlans, setVlans] = useState<VlanOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDevice, setEditingDevice] = useState<MabDevice | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => { loadDevices(); loadVlans(); }, []);

  const loadVlans = async () => {
    try {
      const res = await api.get('/vlans');
      setVlans(res.data.items || []);
    } catch { /* ignore */ }
  };

  const loadDevices = async (p = 1) => {
    setLoading(true);
    try {
      const res = await api.get('/mab-devices', { params: { page: p, page_size: 50 } });
      setDevices(res.data.items || []);
      setTotal(res.data.total || 0);
      setPage(p);
    } catch { message.error('Failed to load MAB devices'); }
    setLoading(false);
  };

  const openCreate = () => {
    setEditingDevice(null);
    form.resetFields();
    form.setFieldsValue({ enabled: true, device_type: 'other' });
    setModalOpen(true);
  };

  const openEdit = (dev: MabDevice) => {
    setEditingDevice(dev);
    form.setFieldsValue({
      mac_address: dev.mac_address,
      name: dev.name,
      description: dev.description,
      device_type: dev.device_type,
      assigned_vlan_id: dev.assigned_vlan_id,
      enabled: dev.enabled,
      expiry_date: dev.expiry_date ? dayjs(dev.expiry_date) : null,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const payload = {
        ...values,
        expiry_date: values.expiry_date ? values.expiry_date.toISOString() : null,
      };
      if (editingDevice) {
        delete payload.mac_address;
        await api.put(`/mab-devices/${editingDevice.id}`, payload);
        message.success('MAB device updated');
      } else {
        await api.post('/mab-devices', payload);
        message.success('MAB device added');
      }
      setModalOpen(false);
      loadDevices(page);
    } catch (err: any) {
      if (err?.response?.data?.detail) message.error(err.response.data.detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/mab-devices/${id}`);
      message.success('MAB device removed');
      loadDevices(page);
    } catch (err) { message.error(extractErrorMessage(err, 'Delete failed')); }
  };

  const getVlanLabel = (vlanId: number | null) => {
    if (!vlanId) return '-';
    const v = vlans.find(vl => vl.vlan_id === vlanId);
    return v ? `${vlanId} - ${v.name}` : String(vlanId);
  };

  const columns: ColumnsType<MabDevice> = [
    {
      title: 'MAC Address', dataIndex: 'mac_address', width: 170,
      render: (mac, record) => (
        <a onClick={() => openEdit(record)} style={{ fontFamily: 'monospace' }}>{mac}</a>
      ),
    },
    { title: 'Name', dataIndex: 'name', width: 180, render: (v) => v || '-' },
    {
      title: 'Device Type', dataIndex: 'device_type', width: 120,
      render: (v) => v ? <Tag color={TYPE_COLORS[v] || 'default'}>{v}</Tag> : '-',
    },
    {
      title: 'Assigned VLAN', dataIndex: 'assigned_vlan_id', width: 150,
      render: (v) => v ? <Tag color="blue">{getVlanLabel(v)}</Tag> : '-',
    },
    {
      title: 'Enabled', dataIndex: 'enabled', width: 80,
      render: (v) => v ? <Badge status="success" text="Yes" /> : <Badge status="default" text="No" />,
    },
    {
      title: 'Expiry', dataIndex: 'expiry_date', width: 120,
      render: (v) => {
        if (!v) return <Tag>Never</Tag>;
        const d = dayjs(v);
        const expired = d.isBefore(dayjs());
        return <Tag color={expired ? 'red' : 'default'}>{d.format('YYYY-MM-DD')}</Tag>;
      },
    },
    {
      title: 'Actions', key: 'actions', width: 100,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="Remove from MAB whitelist?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>MAB Device Whitelist</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>Add Device</Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadDevices(page)}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={devices}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{
            current: page,
            total,
            pageSize: 50,
            onChange: (p) => loadDevices(p),
            showTotal: (t) => `Total: ${t}`,
          }}
        />
      </Card>

      <Modal
        title={editingDevice ? 'Edit MAB Device' : 'Add MAB Device'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={520}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="mac_address" label="MAC Address"
            rules={[{ required: true, message: 'MAC address is required' }]}>
            <Input
              placeholder="AA:BB:CC:DD:EE:FF"
              disabled={!!editingDevice}
              style={{ fontFamily: 'monospace' }}
            />
          </Form.Item>
          <Form.Item name="name" label="Name">
            <Input placeholder="e.g., Office Printer 3F" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="device_type" label="Device Type">
              <Select style={{ width: 160 }}>
                {DEVICE_TYPES.map(t => (
                  <Select.Option key={t.value} value={t.value}>{t.label}</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item name="assigned_vlan_id" label="Assigned VLAN">
              <Select placeholder="Select VLAN" allowClear style={{ width: 200 }}>
                {vlans.map(v => (
                  <Select.Option key={v.vlan_id} value={v.vlan_id}>
                    {v.vlan_id} - {v.name}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
          </Space>
          <Form.Item name="description" label="Description">
            <TextArea rows={2} placeholder="Optional description" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="enabled" label="Enabled" valuePropName="checked">
              <AntSwitch />
            </Form.Item>
            <Form.Item name="expiry_date" label="Expiry Date">
              <DatePicker style={{ width: 200 }} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
