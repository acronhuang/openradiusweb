import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Input,
  Select, DatePicker, Modal, Descriptions, message, Row, Col,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, InfoCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import api from '../../api';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

interface AuditEntry {
  id: string;
  timestamp: string;
  username: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  ip_address?: string;
  details?: Record<string, any>;
}

const ACTION_OPTIONS = [
  { value: 'create', label: 'Create' },
  { value: 'update', label: 'Update' },
  { value: 'delete', label: 'Delete' },
  { value: 'login', label: 'Login' },
  { value: 'reset_password', label: 'Reset Password' },
  { value: 'freeradius_config_apply', label: 'FreeRADIUS Config Apply' },
];

const RESOURCE_TYPE_OPTIONS = [
  { value: 'user', label: 'User' },
  { value: 'certificate', label: 'Certificate' },
  { value: 'ldap_server', label: 'LDAP Server' },
  { value: 'radius_realm', label: 'RADIUS Realm' },
  { value: 'nas_client', label: 'NAS Client' },
  { value: 'device', label: 'Device' },
  { value: 'policy', label: 'Policy' },
  { value: 'freeradius_config', label: 'FreeRADIUS Config' },
];

const ACTION_COLORS: Record<string, string> = {
  create: 'green',
  update: 'blue',
  delete: 'red',
  login: 'cyan',
  reset_password: 'orange',
  freeradius_config_apply: 'purple',
};

const RESOURCE_COLORS: Record<string, string> = {
  user: 'blue',
  certificate: 'gold',
  ldap_server: 'purple',
  radius_realm: 'cyan',
  nas_client: 'green',
  device: 'geekblue',
  policy: 'magenta',
  freeradius_config: 'volcano',
};

export default function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // Filters
  const [search, setSearch] = useState('');
  const [actionFilter, setActionFilter] = useState<string | undefined>(undefined);
  const [resourceFilter, setResourceFilter] = useState<string | undefined>(undefined);
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null);

  // Detail modal
  const [detailVisible, setDetailVisible] = useState(false);
  const [selectedEntry, setSelectedEntry] = useState<AuditEntry | null>(null);

  useEffect(() => {
    loadAuditLog();
  }, [page, pageSize]);

  const loadAuditLog = async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { page, page_size: pageSize };
      if (search) params.search = search;
      if (actionFilter) params.action = actionFilter;
      if (resourceFilter) params.resource_type = resourceFilter;
      if (dateRange && dateRange[0]) params.start_date = dateRange[0].toISOString();
      if (dateRange && dateRange[1]) params.end_date = dateRange[1].toISOString();

      const res = await api.get('/audit-log', { params });
      setEntries(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch { message.error('Failed to load audit log'); }
    setLoading(false);
  };

  const handleSearch = () => {
    setPage(1);
    loadAuditLog();
  };

  const handleReset = () => {
    setSearch('');
    setActionFilter(undefined);
    setResourceFilter(undefined);
    setDateRange(null);
    setPage(1);
    // Reload after state update
    setTimeout(() => loadAuditLog(), 0);
  };

  const openDetail = (entry: AuditEntry) => {
    setSelectedEntry(entry);
    setDetailVisible(true);
  };

  const getDescription = (entry: AuditEntry): string => {
    if (entry.details?.description) return entry.details.description;
    if (entry.details?.message) return entry.details.message;
    return `${entry.action} ${entry.resource_type}${entry.resource_id ? ` (${entry.resource_id})` : ''}`;
  };

  const columns: ColumnsType<AuditEntry> = [
    {
      title: 'Timestamp', dataIndex: 'timestamp', width: 170,
      render: (v) => v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: 'User', dataIndex: 'username', width: 120,
    },
    {
      title: 'Action', dataIndex: 'action', width: 140,
      render: (v) => <Tag color={ACTION_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: 'Resource Type', dataIndex: 'resource_type', width: 140,
      render: (v) => <Tag color={RESOURCE_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: 'Description', key: 'description',
      render: (_, record) => (
        <Text ellipsis style={{ maxWidth: 300 }}>{getDescription(record)}</Text>
      ),
    },
    {
      title: 'IP Address', dataIndex: 'ip_address', width: 130,
      render: (v) => v || '-',
    },
    {
      title: '', key: 'detail', width: 40,
      render: (_, record) => (
        <InfoCircleOutlined
          style={{ color: '#1677ff', cursor: 'pointer' }}
          onClick={(e) => { e.stopPropagation(); openDetail(record); }}
        />
      ),
    },
  ];

  const renderDetailChanges = () => {
    if (!selectedEntry?.details) return null;

    const { old_value, new_value, changed_fields } = selectedEntry.details;

    return (
      <>
        {changed_fields && Array.isArray(changed_fields) && changed_fields.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <Title level={5}>Changed Fields</Title>
            <Space wrap>
              {changed_fields.map((field: string) => (
                <Tag key={field} color="blue">{field}</Tag>
              ))}
            </Space>
          </div>
        )}

        {(old_value || new_value) && (
          <div style={{ marginTop: 16 }}>
            <Title level={5}>Changes</Title>
            <Row gutter={16}>
              <Col span={12}>
                <Card size="small" title="Before" style={{ background: '#fff2f0' }}>
                  <pre style={{
                    fontSize: 11, maxHeight: 300, overflow: 'auto',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>
                    {JSON.stringify(old_value, null, 2) || 'N/A'}
                  </pre>
                </Card>
              </Col>
              <Col span={12}>
                <Card size="small" title="After" style={{ background: '#f6ffed' }}>
                  <pre style={{
                    fontSize: 11, maxHeight: 300, overflow: 'auto',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>
                    {JSON.stringify(new_value, null, 2) || 'N/A'}
                  </pre>
                </Card>
              </Col>
            </Row>
          </div>
        )}
      </>
    );
  };

  return (
    <div>
      <Title level={4}>Audit Log</Title>

      {/* Filter Bar */}
      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="Search action, resource..."
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onPressEnter={handleSearch}
            style={{ width: 250 }}
          />
          <Select
            placeholder="Action"
            allowClear
            value={actionFilter}
            onChange={setActionFilter}
            options={ACTION_OPTIONS}
            style={{ width: 180 }}
          />
          <Select
            placeholder="Resource Type"
            allowClear
            value={resourceFilter}
            onChange={setResourceFilter}
            options={RESOURCE_TYPE_OPTIONS}
            style={{ width: 180 }}
          />
          <RangePicker
            value={dateRange as any}
            onChange={(dates) => setDateRange(dates as any)}
            showTime
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
            Search
          </Button>
          <Button icon={<ReloadOutlined />} onClick={handleReset}>
            Reset
          </Button>
        </Space>
      </Card>

      {/* Audit Table */}
      <Card>
        <Table
          columns={columns}
          dataSource={entries}
          rowKey="id"
          loading={loading}
          size="small"
          onRow={(record) => ({
            onClick: () => openDetail(record),
            style: { cursor: 'pointer' },
          })}
          pagination={{
            current: page,
            total,
            pageSize,
            onChange: (p, ps) => { setPage(p); setPageSize(ps); },
            showTotal: (t) => `Total: ${t}`,
            showSizeChanger: true,
            pageSizeOptions: ['10', '20', '50', '100'],
          }}
        />
      </Card>

      {/* Detail Modal */}
      <Modal
        title="Audit Log Detail"
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={null}
        width={800}
      >
        {selectedEntry && (
          <>
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="Timestamp">
                {selectedEntry.timestamp ? new Date(selectedEntry.timestamp).toLocaleString() : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="User">
                {selectedEntry.username}
              </Descriptions.Item>
              <Descriptions.Item label="Action">
                <Tag color={ACTION_COLORS[selectedEntry.action] || 'default'}>
                  {selectedEntry.action}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Resource Type">
                <Tag color={RESOURCE_COLORS[selectedEntry.resource_type] || 'default'}>
                  {selectedEntry.resource_type}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Resource ID" span={2}>
                {selectedEntry.resource_id || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="IP Address">
                {selectedEntry.ip_address || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="Description">
                {getDescription(selectedEntry)}
              </Descriptions.Item>
            </Descriptions>

            {renderDetailChanges()}
          </>
        )}
      </Modal>
    </div>
  );
}
