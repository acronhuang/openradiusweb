import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Switch, Badge, Modal,
  Form, Input, InputNumber, Select, message, Popconfirm, Divider
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, ThunderboltOutlined,
  DeleteOutlined, EditOutlined, MinusCircleOutlined
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../api';

const { Title } = Typography;
const { TextArea } = Input;
const { Option } = Select;

interface PolicyCondition {
  field: string;
  operator: string;
  value: any;
}

interface PolicyAction {
  type: string;
  params: Record<string, any>;
}

interface Policy {
  id: string;
  name: string;
  description: string;
  priority: number;
  enabled: boolean;
  conditions: PolicyCondition[];
  match_actions: PolicyAction[];
  no_match_actions: PolicyAction[];
  created_at: string;
  updated_at: string;
}

const CONDITION_FIELDS = [
  { value: 'auth.method', label: 'Auth Method' },
  { value: 'auth.result', label: 'Auth Result' },
  { value: 'device.type', label: 'Device Type' },
  { value: 'device.os_family', label: 'OS Family' },
  { value: 'device.status', label: 'Device Status' },
  { value: 'device.risk_score', label: 'Risk Score' },
  { value: 'device.vendor', label: 'Device Vendor' },
  { value: 'network.nas_ip', label: 'NAS IP' },
  { value: 'network.vlan', label: 'VLAN' },
  { value: 'user.domain', label: 'User Domain' },
  { value: 'user.group', label: 'User Group' },
];

const OPERATORS = [
  { value: 'equals', label: '=' },
  { value: 'not_equals', label: '!=' },
  { value: 'in', label: 'in' },
  { value: 'not_in', label: 'not in' },
  { value: 'contains', label: 'contains' },
  { value: 'gt', label: '>' },
  { value: 'lt', label: '<' },
  { value: 'regex', label: 'regex' },
];

const ACTION_TYPES = [
  { value: 'vlan_assign', label: 'VLAN Assign', color: 'blue' },
  { value: 'acl_apply', label: 'ACL Apply', color: 'cyan' },
  { value: 'quarantine', label: 'Quarantine', color: 'red' },
  { value: 'reject', label: 'Reject', color: 'red' },
  { value: 'coa', label: 'CoA', color: 'orange' },
  { value: 'notify', label: 'Notify', color: 'purple' },
  { value: 'captive_portal', label: 'Captive Portal', color: 'gold' },
  { value: 'create_incident', label: 'Create Incident', color: 'magenta' },
  { value: 'bounce_port', label: 'Bounce Port', color: 'volcano' },
  { value: 'tag_device', label: 'Tag Device', color: 'green' },
  { value: 'log', label: 'Log', color: 'default' },
];

// Predefined value options for each condition field
const FIELD_VALUE_OPTIONS: Record<string, string[]> = {
  'auth.method': ['802.1X', 'MAB', 'Web Auth', 'RADIUS', 'LDAP', 'Certificate'],
  'auth.result': ['authenticated', 'failed', 'timeout', 'rejected', 'unknown'],
  'device.type': ['workstation', 'server', 'printer', 'phone', 'camera', 'iot', 'switch', 'router', 'access_point', 'unknown'],
  'device.os_family': ['Windows', 'macOS', 'Linux', 'iOS', 'Android', 'ChromeOS', 'Unknown'],
  'device.status': ['authenticated', 'unauthenticated', 'quarantined', 'guest', 'blocked', 'unknown'],
  'device.vendor': [], // free text
  'device.risk_score': [], // numeric
  'network.nas_ip': [], // free text (IP)
  'network.vlan': [], // numeric
  'user.domain': [], // free text
  'user.group': [], // free text
};

// Action param templates
const ACTION_PARAM_FIELDS: Record<string, { key: string; label: string; type: 'number' | 'text' | 'select' | 'vlan_select'; options?: string[] }[]> = {
  'vlan_assign': [{ key: 'vlan_id', label: 'VLAN', type: 'vlan_select' }],
  'acl_apply': [{ key: 'acl', label: 'ACL Name', type: 'select', options: ['full_access', 'limited_access', 'guest_access', 'quarantine', 'deny_all'] }],
  'quarantine': [
    { key: 'vlan_id', label: 'Quarantine VLAN', type: 'vlan_select' },
    { key: 'reason', label: 'Reason', type: 'select', options: ['non_compliant', 'unknown_device', 'security_risk', 'policy_violation'] },
  ],
  'reject': [{ key: 'reason', label: 'Reason', type: 'text' }],
  'coa': [
    { key: 'action', label: 'CoA Action', type: 'select', options: ['reauthenticate', 'disconnect', 'bounce_port', 'change_vlan'] },
    { key: 'vlan_id', label: 'New VLAN', type: 'vlan_select' },
  ],
  'notify': [
    { key: 'method', label: 'Method', type: 'select', options: ['email', 'syslog', 'webhook', 'snmp_trap'] },
    { key: 'message', label: 'Message', type: 'text' },
  ],
  'captive_portal': [
    { key: 'portal_url', label: 'Portal URL', type: 'text' },
    { key: 'redirect_url', label: 'Redirect URL', type: 'text' },
  ],
  'create_incident': [
    { key: 'severity', label: 'Severity', type: 'select', options: ['low', 'medium', 'high', 'critical'] },
    { key: 'description', label: 'Description', type: 'text' },
  ],
  'bounce_port': [{ key: 'delay_seconds', label: 'Delay (sec)', type: 'number' }],
  'tag_device': [{ key: 'tag', label: 'Tag Name', type: 'text' }],
  'log': [
    { key: 'level', label: 'Log Level', type: 'select', options: ['info', 'warning', 'error'] },
    { key: 'message', label: 'Message', type: 'text' },
  ],
};

// Component for condition value input - shows dropdown or free text
function ConditionValueInput({ fieldName, value, onChange }: { fieldName: string; value?: string; onChange?: (v: string) => void }) {
  const options = FIELD_VALUE_OPTIONS[fieldName] || [];
  if (options.length > 0) {
    return (
      <Select
        style={{ width: 220 }}
        placeholder="Select value"
        value={value || undefined}
        onChange={onChange}
        allowClear
        showSearch
        mode={undefined}
      >
        {options.map(o => <Option key={o} value={o}>{o}</Option>)}
      </Select>
    );
  }
  // Numeric fields
  if (['device.risk_score', 'network.vlan'].includes(fieldName)) {
    return <InputNumber style={{ width: 220 }} placeholder="Enter number" value={value ? Number(value) : undefined} onChange={(v) => onChange?.(String(v ?? ''))} />;
  }
  return <Input style={{ width: 220 }} placeholder="Enter value" value={value} onChange={(e) => onChange?.(e.target.value)} />;
}

// Component for action params - structured inputs instead of raw JSON
function ActionParamsInput({ actionType, value, onChange, vlans }: { actionType: string; value?: string; onChange?: (v: string) => void; vlans?: { vlan_id: number; name: string }[] }) {
  const fields = ACTION_PARAM_FIELDS[actionType];
  if (!fields || fields.length === 0) {
    return <Input placeholder='{"key": "value"}' style={{ width: 350 }} value={value} onChange={(e) => onChange?.(e.target.value)} />;
  }

  let params: Record<string, any> = {};
  try { params = JSON.parse(value || '{}'); } catch { /* keep empty */ }

  const updateParam = (key: string, val: any) => {
    const updated = { ...params, [key]: val };
    // Remove empty values
    Object.keys(updated).forEach(k => { if (updated[k] === '' || updated[k] === undefined || updated[k] === null) delete updated[k]; });
    onChange?.(JSON.stringify(updated));
  };

  return (
    <Space wrap size={[8, 4]} style={{ width: 350 }}>
      {fields.map(f => {
        if (f.type === 'vlan_select') {
          return (
            <Select
              key={f.key}
              placeholder={f.label}
              value={params[f.key] || undefined}
              onChange={(v) => updateParam(f.key, v)}
              style={{ width: fields.length === 1 ? 342 : 165 }}
              allowClear
              showSearch
              optionFilterProp="children"
            >
              {(vlans || []).map(v => (
                <Option key={v.vlan_id} value={v.vlan_id}>{v.vlan_id} - {v.name}</Option>
              ))}
            </Select>
          );
        }
        if (f.type === 'number') {
          return (
            <InputNumber
              key={f.key}
              placeholder={f.label}
              value={params[f.key]}
              onChange={(v) => updateParam(f.key, v)}
              style={{ width: fields.length === 1 ? 342 : 165 }}
              addonBefore={fields.length > 1 ? f.label : undefined}
            />
          );
        }
        if (f.type === 'select' && f.options) {
          return (
            <Select
              key={f.key}
              placeholder={f.label}
              value={params[f.key] || undefined}
              onChange={(v) => updateParam(f.key, v)}
              style={{ width: fields.length === 1 ? 342 : 165 }}
              allowClear
            >
              {f.options.map(o => <Option key={o} value={o}>{o}</Option>)}
            </Select>
          );
        }
        return (
          <Input
            key={f.key}
            placeholder={f.label}
            value={params[f.key] || ''}
            onChange={(e) => updateParam(f.key, e.target.value)}
            style={{ width: fields.length === 1 ? 342 : 165 }}
          />
        );
      })}
    </Space>
  );
}

export default function Policies() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<Policy | null>(null);
  const [saving, setSaving] = useState(false);
  const [vlans, setVlans] = useState<{ vlan_id: number; name: string }[]>([]);
  const [form] = Form.useForm();

  useEffect(() => { loadPolicies(); loadVlans(); }, []);

  const loadVlans = async () => {
    try {
      const res = await api.get('/vlans');
      setVlans(res.data.items || []);
    } catch { /* ignore */ }
  };

  const loadPolicies = async () => {
    setLoading(true);
    try {
      const res = await api.get('/policies', { params: { page_size: 100 } });
      setPolicies(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load policies')); }
    setLoading(false);
  };

  const openCreate = () => {
    setEditingPolicy(null);
    form.resetFields();
    form.setFieldsValue({
      priority: 100,
      enabled: true,
      conditions: [{ field: 'auth.result', operator: 'equals', value: 'authenticated' }],
      match_actions: [{ type: 'vlan_assign', params_json: '{"vlan_id": 100}' }],
      no_match_actions: [],
    });
    setModalOpen(true);
  };

  const openEdit = (policy: Policy) => {
    setEditingPolicy(policy);
    form.setFieldsValue({
      name: policy.name,
      description: policy.description,
      priority: policy.priority,
      enabled: policy.enabled,
      conditions: policy.conditions.map(c => ({
        field: c.field,
        operator: c.operator,
        value: Array.isArray(c.value) ? JSON.stringify(c.value) : String(c.value),
      })),
      match_actions: policy.match_actions.map(a => ({
        type: a.type,
        params_json: JSON.stringify(a.params || {}),
      })),
      no_match_actions: (policy.no_match_actions || []).map(a => ({
        type: a.type,
        params_json: JSON.stringify(a.params || {}),
      })),
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);

      // Parse conditions values
      const conditions = (values.conditions || []).map((c: any) => {
        let val = c.value;
        try { val = JSON.parse(val); } catch { /* keep as string */ }
        return { field: c.field, operator: c.operator, value: val };
      });

      // Parse action params
      const parseActions = (actions: any[]) =>
        (actions || []).map((a: any) => {
          let params = {};
          try { params = JSON.parse(a.params_json || '{}'); } catch { /* empty */ }
          return { type: a.type, params };
        });

      const payload = {
        name: values.name,
        description: values.description || '',
        priority: values.priority,
        enabled: values.enabled,
        conditions,
        match_actions: parseActions(values.match_actions),
        no_match_actions: parseActions(values.no_match_actions),
      };

      if (editingPolicy) {
        await api.patch(`/policies/${editingPolicy.id}`, payload);
        message.success('Policy updated');
      } else {
        await api.post('/policies', payload);
        message.success('Policy created');
      }
      setModalOpen(false);
      loadPolicies();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to save policy'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/policies/${id}`);
      message.success('Policy deleted');
      loadPolicies();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Delete failed'));
    }
  };

  const handleToggle = async (policy: Policy, enabled: boolean) => {
    try {
      await api.patch(`/policies/${policy.id}`, { enabled });
      message.success(`Policy ${enabled ? 'enabled' : 'disabled'}`);
      loadPolicies();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Update failed'));
    }
  };

  const actionColor: Record<string, string> = {};
  ACTION_TYPES.forEach(a => { actionColor[a.value] = a.color; });

  const columns: ColumnsType<Policy> = [
    { title: 'Priority', dataIndex: 'priority', width: 80, sorter: (a, b) => a.priority - b.priority,
      defaultSortOrder: 'ascend' },
    { title: 'Name', dataIndex: 'name', width: 220,
      render: (name, record) => (
        <a onClick={() => openEdit(record)}>{name}</a>
      ),
    },
    { title: 'Description', dataIndex: 'description', ellipsis: true },
    { title: 'Conditions', dataIndex: 'conditions', width: 100,
      render: (v) => <Badge count={v?.length || 0} style={{ backgroundColor: '#1677ff' }} /> },
    { title: 'Actions', dataIndex: 'match_actions', width: 200,
      render: (actions: any[]) => (
        <Space wrap size={[4, 4]}>
          {(actions || []).map((a: any, i: number) => (
            <Tag key={i} color={actionColor[a.type] || 'default'}>{a.type}</Tag>
          ))}
        </Space>
      ),
    },
    { title: 'Enabled', dataIndex: 'enabled', width: 80,
      render: (v, record) => (
        <Switch checked={v} size="small" onChange={(checked) => handleToggle(record, checked)} />
      ),
    },
    { title: 'Actions', key: 'ops', width: 100,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="Delete this policy?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>Policies</Title>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>New Policy</Button>
          <Button icon={<ReloadOutlined />} onClick={loadPolicies}>Refresh</Button>
        </Space>
        <Table columns={columns} dataSource={policies} rowKey="id"
          loading={loading} size="small"
          pagination={{ total, pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      {/* Create/Edit Policy Modal */}
      <Modal
        title={editingPolicy ? 'Edit Policy' : 'New Policy'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={780}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
            <Input placeholder="e.g., Corporate Device Access" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <TextArea rows={2} placeholder="Policy description" />
          </Form.Item>
          <Space>
            <Form.Item name="priority" label="Priority" rules={[{ required: true }]}>
              <InputNumber min={1} max={10000} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="enabled" label="Enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>

          <Divider orientation="left" plain>Conditions</Divider>
          <Form.List name="conditions">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item {...rest} name={[name, 'field']} rules={[{ required: true }]}>
                      <Select style={{ width: 160 }} placeholder="Field">
                        {CONDITION_FIELDS.map(f => <Option key={f.value} value={f.value}>{f.label}</Option>)}
                      </Select>
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'operator']} rules={[{ required: true }]}>
                      <Select style={{ width: 100 }} placeholder="Op">
                        {OPERATORS.map(o => <Option key={o.value} value={o.value}>{o.label}</Option>)}
                      </Select>
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate={(prev, cur) => {
                      const pf = prev.conditions?.[name]?.field;
                      const cf = cur.conditions?.[name]?.field;
                      return pf !== cf;
                    }}>
                      {() => {
                        const fieldVal = form.getFieldValue(['conditions', name, 'field']) || '';
                        return (
                          <Form.Item {...rest} name={[name, 'value']} rules={[{ required: true }]}>
                            <ConditionValueInput fieldName={fieldVal} />
                          </Form.Item>
                        );
                      }}
                    </Form.Item>
                    <MinusCircleOutlined onClick={() => remove(name)} />
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add({ field: 'auth.result', operator: 'equals', value: '' })}
                  icon={<PlusOutlined />} style={{ width: '100%' }}>
                  Add Condition
                </Button>
              </>
            )}
          </Form.List>

          <Divider orientation="left" plain>Match Actions</Divider>
          <Form.List name="match_actions">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item {...rest} name={[name, 'type']} rules={[{ required: true }]}>
                      <Select style={{ width: 160 }} placeholder="Action Type">
                        {ACTION_TYPES.map(a => <Option key={a.value} value={a.value}>{a.label}</Option>)}
                      </Select>
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate={(prev, cur) => {
                      const pt = prev.match_actions?.[name]?.type;
                      const ct = cur.match_actions?.[name]?.type;
                      return pt !== ct;
                    }}>
                      {() => {
                        const actionType = form.getFieldValue(['match_actions', name, 'type']) || '';
                        return (
                          <Form.Item {...rest} name={[name, 'params_json']}>
                            <ActionParamsInput actionType={actionType} vlans={vlans} />
                          </Form.Item>
                        );
                      }}
                    </Form.Item>
                    <MinusCircleOutlined onClick={() => remove(name)} />
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add({ type: 'vlan_assign', params_json: '{"vlan_id": 100}' })}
                  icon={<PlusOutlined />} style={{ width: '100%' }}>
                  Add Match Action
                </Button>
              </>
            )}
          </Form.List>

          <Divider orientation="left" plain>No-Match Actions</Divider>
          <Form.List name="no_match_actions">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item {...rest} name={[name, 'type']} rules={[{ required: true }]}>
                      <Select style={{ width: 160 }} placeholder="Action Type">
                        {ACTION_TYPES.map(a => <Option key={a.value} value={a.value}>{a.label}</Option>)}
                      </Select>
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate={(prev, cur) => {
                      const pt = prev.no_match_actions?.[name]?.type;
                      const ct = cur.no_match_actions?.[name]?.type;
                      return pt !== ct;
                    }}>
                      {() => {
                        const actionType = form.getFieldValue(['no_match_actions', name, 'type']) || '';
                        return (
                          <Form.Item {...rest} name={[name, 'params_json']}>
                            <ActionParamsInput actionType={actionType} vlans={vlans} />
                          </Form.Item>
                        );
                      }}
                    </Form.Item>
                    <MinusCircleOutlined onClick={() => remove(name)} />
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add({ type: 'quarantine', params_json: '{}' })}
                  icon={<PlusOutlined />} style={{ width: '100%' }}>
                  Add No-Match Action
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Modal>
    </div>
  );
}
